#!/usr/bin/env python3
"""
Бот, который следит за поисковыми выдачами на halooglasi.com, cityexpert.rs,
nekretnine.rs и kupujemprodajem.com и присылает в Telegram только новые объявления.

Работает в двух режимах:
  в облаке (GitHub Actions):  python bot.py --once   — один проход, запускается по расписанию
  на своём компьютере:        python bot.py          — крутится в цикле

Дополнительно:
  python bot.py --debug   один проход: показать, что найдено, ничего не отправлять
  python bot.py --test    отправить тестовое сообщение
"""
import argparse
import html
import json
import logging
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import config

BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / "seen.json"
DEBUG_DIR = BASE_DIR / "debug"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("bot")


# ------------------------------------------------------------------
#  Как распознать ссылку на объявление на каждом сайте
# ------------------------------------------------------------------
def _id_halooglasi(path: str):
    # /nekretnine/izdavanje-stanova/<slug>/5425647712295
    m = re.fullmatch(r"/nekretnine/izdavanje-[a-z-]+/[^/]+/(\d{8,})", path)
    return m.group(1) if m else None


def _id_cityexpert(path: str):
    # /izdavanje-nekretnina/beograd/10815/jednoiposoban-stan-zorina-cukarica
    m = re.fullmatch(r"/izdavanje-nekretnina/[a-z-]+/(\d+)/[^/]+", path)
    return m.group(1) if m else None


def _id_nekretnine(path: str):
    # Формат ссылок на nekretnine.rs менялся, поэтому правило широкое:
    # последний сегмент пути — ID из цифр (возможно с буквенным префиксом).
    segs = [s for s in path.split("/") if s]
    if not segs:
        return None
    if segs[0].startswith(("izdavanje-", "prodaja-", "agencij", "terms",
                           "postavite", "novogradnja", "cene")):
        return None
    last = segs[-1]
    if re.fullmatch(r"[A-Za-z]{0,4}\d{5,}", last):
        return last
    return None


def _id_kupujemprodajem(path: str):
    # /nekretnine-izdavanje/stanovi-dvosobni/izdavanje-stana-medakovic/oglas/181720879
    m = re.fullmatch(r"/[a-z-]+/[a-z0-9-]+/[^/]+/oglas/(\d{6,})", path.lower())
    return m.group(1) if m else None


SITES = {
    "halooglasi": ("halooglasi.com", _id_halooglasi),
    "cityexpert": ("cityexpert.rs", _id_cityexpert),
    "nekretnine": ("nekretnine.rs", _id_nekretnine),
    "kupujemprodajem": ("kupujemprodajem.com", _id_kupujemprodajem),
}


# ------------------------------------------------------------------
#  Разбор страницы выдачи
# ------------------------------------------------------------------
_NUM = r"(?<![\d.,])(\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{2})?|\d+)"
PRICE_RE = re.compile(r"€\s*" + _NUM + r"|" + _NUM + r"\s*(?:€|EUR|eur)")


def _parse_price(text: str):
    for m in PRICE_RE.finditer(text):
        raw = (m.group(1) or m.group(2)).strip()
        raw = re.sub(r"[.,]\d{2}$", "", raw)       # 1,000.00 -> 1,000
        digits = re.sub(r"\D", "", raw)
        if digits and 30 <= int(digits) <= 100000:
            return int(digits)
    return None


def extract_listings(page_html: str, base_url: str, site: str):
    domain, id_fn = SITES[site]
    soup = BeautifulSoup(page_html, "html.parser")

    anchor_id = {}          # id(tag) -> listing id
    by_id, order = {}, []
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"])
        u = urlparse(full)
        if not u.netloc.endswith(domain):
            continue
        lid = id_fn(u.path.rstrip("/"))
        if not lid:
            continue
        anchor_id[id(a)] = lid
        if lid not in by_id:
            by_id[lid] = {"anchors": [], "link": f"{u.scheme}://{u.netloc}{u.path}"}
            order.append(lid)
        by_id[lid]["anchors"].append(a)

    listings = []
    for lid in order:
        anchors = by_id[lid]["anchors"]
        # Поднимаемся от ссылки вверх, пока блок содержит только это объявление:
        # так находим «карточку» без привязки к CSS-классам сайта.
        card, node = anchors[0], anchors[0].parent
        while node is not None and node.name not in ("body", "html", "[document]"):
            ids = {anchor_id[id(x)] for x in node.find_all("a", href=True)
                   if id(x) in anchor_id}
            if len(ids) > 1:
                break
            card, node = node, node.parent

        text = card.get_text(" ", strip=True)
        titles = [a.get_text(" ", strip=True) for a in anchors]
        title = max(titles, key=len) if any(titles) else ""
        if not title:
            title = text[:90]
        title = PRICE_RE.sub("", title, count=1).strip(" ·-|,") or title

        image = None
        for img in card.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if src and not src.startswith("data:") and "logo" not in src.lower():
                image = urljoin(base_url, src)
                break

        listings.append({
            "id": lid,
            "link": by_id[lid]["link"],
            "title": title,
            "price": _parse_price(text),
            "text": text,
            "image": image,
        })
    return listings


# ------------------------------------------------------------------
#  Telegram
# ------------------------------------------------------------------
def tg(method: str, **data):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/{method}"
    for _ in range(3):
        try:
            r = requests.post(url, data=data, timeout=30)
            j = r.json()
        except Exception as e:
            log.warning("Telegram недоступен: %s", e)
            time.sleep(5)
            continue
        if j.get("ok"):
            return j
        if r.status_code == 429:
            time.sleep(j.get("parameters", {}).get("retry_after", 5) + 1)
            continue
        log.warning("Telegram ответил ошибкой: %s", j.get("description"))
        return j
    return {}


def send_text(text: str):
    tg("sendMessage", chat_id=config.TELEGRAM_CHAT_ID, text=text,
       parse_mode="HTML", disable_web_page_preview="true")


def send_listing(item: dict, search_name: str):
    e = html.escape
    price = f"{item['price']} €" if item["price"] else "цена ?"
    snippet = item["text"]
    if item["title"] and item["title"] in snippet:
        snippet = snippet.replace(item["title"], "", 1)
    snippet = PRICE_RE.sub("", snippet, count=1).strip(" ·-|,")
    snippet = snippet[:350] + ("…" if len(snippet) > 350 else "")
    caption = (f"<b>{e(price)}</b> · {e(item['title'][:150])}\n\n"
               + (f"{e(snippet)}\n\n" if snippet else "") +
               f"<a href=\"{e(item['link'])}\">Открыть объявление</a>\n"
               f"<i>{e(search_name)}</i>")
    if item["image"]:
        res = tg("sendPhoto", chat_id=config.TELEGRAM_CHAT_ID,
                 photo=item["image"], caption=caption, parse_mode="HTML")
        if res.get("ok"):
            return
    send_text(caption)


# ------------------------------------------------------------------
#  Загрузка страниц: сначала быстрым запросом, если не вышло — через браузер
# ------------------------------------------------------------------
class Fetcher:
    def __init__(self):
        self._pw = self._browser = self._ctx = None
        self.last_title = ""
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept-Language": "sr-RS,sr;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        })

    def plain(self, url: str) -> str:
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        return r.text

    def _start(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"])
        self._ctx = self._browser.new_context(
            user_agent=UA, locale="sr-RS", timezone_id="Europe/Belgrade",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "sr-RS,sr;q=0.9,en;q=0.8"})
        # прячем признаки автоматизации, по которым сайты отличают бота
        self._ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['sr-RS','sr','en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            window.chrome = window.chrome || {runtime: {}};
        """)

    def _open(self, page, url):
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

    def browser(self, url: str, site: str = None) -> str:
        if self._ctx is None:
            self._start()
        page = self._ctx.new_page()
        try:
            self._open(page, url)
            # если попали на страницу проверки защиты — ждём до 25 секунд,
            # пока она сама не пропустит на выдачу
            for _ in range(5):
                if not site or extract_listings(page.content(), url, site):
                    break
                page.wait_for_timeout(5000)
            else:
                # последняя попытка: зайти на главную, получить cookies и вернуться
                home = f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
                self._open(page, home)
                page.wait_for_timeout(6000)
                self._open(page, url)
                page.wait_for_timeout(4000)
            for _ in range(3):
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(700)
            self.last_title = page.title()
            return page.content()
        finally:
            page.close()

    def close(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass


def fetch_listings(fetcher, url, site):
    """Возвращает (объявления, html, способ)."""
    page_html = ""
    try:
        page_html = fetcher.plain(url)
        items = extract_listings(page_html, url, site)
        if items:
            return items, page_html, "запрос"
    except Exception as e:
        log.info("%s: быстрый запрос не удался (%s), пробую браузер", site, e)
    try:
        page_html = fetcher.browser(url, site)
        items = extract_listings(page_html, url, site)
        if not items:
            log.info("%s: браузер открыл страницу «%s», объявлений на ней нет",
                     site, fetcher.last_title[:80])
        return items, page_html, "браузер"
    except Exception as e:
        log.warning("%s: браузер тоже не смог: %s", site, e)
        return [], page_html, "ошибка"


# ------------------------------------------------------------------
#  Память бота: файл seen.json
# ------------------------------------------------------------------
def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"seen": {}, "searches": [], "empty": {}}


def save_state(state):
    # забываем объявления старше 90 дней, чтобы файл не рос бесконечно
    cutoff = (datetime.now() - timedelta(days=90)).isoformat()
    state["seen"] = {k: v for k, v in state["seen"].items() if v >= cutoff}
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=0,
                                     sort_keys=True), encoding="utf-8")


# ------------------------------------------------------------------
#  Проверка одного поиска
# ------------------------------------------------------------------
def check_search(fetcher, state, s, debug=False):
    name, site, url = s["name"], s["site"], s["url"]
    items, page_html, how = fetch_listings(fetcher, url, site)

    if debug:
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / f"{site}.html").write_text(page_html or "", encoding="utf-8")
        print(f"\n=== {name}: найдено {len(items)} объявлений (способ: {how})")
        for it in items[:8]:
            print(f"  {it['price'] or '?':>6} €  {it['title'][:70]}\n          {it['link']}")
        return

    now = datetime.now().isoformat(timespec="seconds")
    if not items:
        n = state["empty"].get(name, 0) + 1
        state["empty"][name] = n
        log.info("%s: 0 объявлений (подряд: %d)", name, n)
        if n == 3:
            send_text(f"⚠️ <b>{html.escape(name)}</b>: три проверки подряд без "
                      f"объявлений. Возможно, сайт блокирует бота или поменял "
                      f"вёрстку. Запустите проверку в режиме debug.")
        return
    state["empty"][name] = 0

    if name not in state["searches"]:
        for it in items:
            state["seen"][f"{site}:{it['id']}"] = now
        state["searches"].append(name)
        send_text(f"✅ Слежу за <b>{html.escape(name)}</b>. Сейчас в выдаче "
                  f"{len(items)} объявлений — буду присылать только новые.")
        log.info("%s: первый запуск, запомнил %d объявлений", name, len(items))
        return

    new = [it for it in items if f"{site}:{it['id']}" not in state["seen"]]
    sent = 0
    for it in reversed(new):
        state["seen"][f"{site}:{it['id']}"] = now
        if config.MAX_PRICE_EUR and it["price"] and it["price"] > config.MAX_PRICE_EUR:
            continue
        if sent >= 15:
            continue
        send_listing(it, name)
        sent += 1
        time.sleep(1.2)
    log.info("%s: %d в выдаче (%s), новых %d, отправлено %d",
             name, len(items), how, len(new), sent)


def run_pass(fetcher, state, debug=False):
    for s in config.SEARCHES:
        try:
            check_search(fetcher, state, s, debug=debug)
        except Exception as e:
            log.warning("%s: ошибка: %s", s["name"], e)
        if not debug:
            save_state(state)
        time.sleep(random.uniform(2, 5))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--test", action="store_true")
    a = p.parse_args()

    if not config.TELEGRAM_TOKEN or "ВСТАВЬТЕ" in config.TELEGRAM_TOKEN:
        sys.exit("Нет TELEGRAM_TOKEN (секрет в GitHub или строка в config.py)")
    if not a.debug and (not config.TELEGRAM_CHAT_ID or "ВСТАВЬТЕ" in str(config.TELEGRAM_CHAT_ID)):
        sys.exit("Нет TELEGRAM_CHAT_ID (секрет в GitHub или строка в config.py)")

    if a.test:
        send_text("🏠 Бот на связи. Как только появятся новые объявления — пришлю.")
        print("Отправлено. Проверьте Telegram.")
        return

    state = load_state()
    fetcher = Fetcher()
    try:
        if a.debug or a.once:
            run_pass(fetcher, state, debug=a.debug)
            return
        while True:
            run_pass(fetcher, state)
            wait = config.CHECK_EVERY_SECONDS + random.uniform(-30, 30)
            time.sleep(max(60, wait))
    finally:
        fetcher.close()


if __name__ == "__main__":
    main()
