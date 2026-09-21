#!/usr/bin/env python3
"""
Бот, который следит за поисковыми выдачами на halooglasi.com, cityexpert.rs,
nekretnine.rs и kupujemprodajem.com и присылает в Telegram только новые объявления.

Запуск:
  python bot.py --chat-id   узнать свой chat id (сначала напишите боту)
  python bot.py --test      отправить тестовое сообщение
  python bot.py --debug     один проход: показать, что найдено, ничего не отправлять
  python bot.py             основной режим (работает, пока открыт Терминал)
"""
import argparse
import html
import logging
import random
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import config

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "seen.sqlite3"
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
#  Загрузка страниц через настоящий браузер (Chromium через Playwright)
# ------------------------------------------------------------------
class Browser:
    def __init__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._ctx = self._browser.new_context(
            user_agent=UA, locale="sr-RS",
            viewport={"width": 1366, "height": 900})

    def get(self, url: str) -> str:
        page = self._ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            for _ in range(3):                       # подгрузить ленивые карточки
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(700)
            return page.content()
        finally:
            page.close()

    def close(self):
        try:
            self._browser.close()
            self._pw.stop()
        except Exception:
            pass


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
    if item["title"] and snippet.startswith(item["title"]):
        snippet = snippet[len(item["title"]):].strip()
    snippet = snippet[:350] + ("…" if len(snippet) > 350 else "")
    caption = (f"<b>{e(price)}</b> · {e(item['title'][:150])}\n\n"
               f"{e(snippet)}\n\n"
               f"<a href=\"{e(item['link'])}\">Открыть объявление</a>\n"
               f"<i>{e(search_name)}</i>")
    if item["image"]:
        res = tg("sendPhoto", chat_id=config.TELEGRAM_CHAT_ID,
                 photo=item["image"], caption=caption, parse_mode="HTML")
        if res.get("ok"):
            return
    send_text(caption)


# ------------------------------------------------------------------
#  База «уже видели»
# ------------------------------------------------------------------
def db_open():
    db = sqlite3.connect(DB_PATH)
    db.execute("CREATE TABLE IF NOT EXISTS seen (site TEXT, lid TEXT, "
               "first_seen TEXT, PRIMARY KEY (site, lid))")
    db.execute("CREATE TABLE IF NOT EXISTS searches (name TEXT PRIMARY KEY)")
    return db


def is_seen(db, site, lid):
    return db.execute("SELECT 1 FROM seen WHERE site=? AND lid=?",
                      (site, lid)).fetchone() is not None


def mark_seen(db, site, lid):
    db.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?)",
               (site, lid, datetime.now().isoformat(timespec="seconds")))


# ------------------------------------------------------------------
#  Основной цикл
# ------------------------------------------------------------------
empty_streak = {}


def check_search(browser, db, s, debug=False):
    name, site, url = s["name"], s["site"], s["url"]
    page_html = browser.get(url)
    items = extract_listings(page_html, url, site)

    if debug:
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / f"{site}.html").write_text(page_html, encoding="utf-8")
        print(f"\n=== {name}: найдено {len(items)} объявлений")
        for it in items[:8]:
            print(f"  {it['price'] or '?':>6} €  {it['title'][:70]}\n          {it['link']}")
        return

    if not items:
        empty_streak[name] = empty_streak.get(name, 0) + 1
        log.info("%s: 0 объявлений (подряд: %d)", name, empty_streak[name])
        if empty_streak[name] == 3:
            send_text(f"⚠️ <b>{html.escape(name)}</b>: три проверки подряд без "
                      f"объявлений. Возможно, сайт блокирует бота или поменял "
                      f"вёрстку. Запустите <code>python bot.py --debug</code>.")
        return
    empty_streak[name] = 0

    first_time = db.execute("SELECT 1 FROM searches WHERE name=?",
                            (name,)).fetchone() is None
    if first_time:
        for it in items:
            mark_seen(db, site, it["id"])
        db.execute("INSERT INTO searches VALUES (?)", (name,))
        db.commit()
        send_text(f"✅ Слежу за <b>{html.escape(name)}</b>. Сейчас в выдаче "
                  f"{len(items)} объявлений — буду присылать только новые.")
        log.info("%s: первый запуск, запомнил %d объявлений", name, len(items))
        return

    new = [it for it in items if not is_seen(db, site, it["id"])]
    sent = 0
    for it in reversed(new):                         # старые из новых — первыми
        mark_seen(db, site, it["id"])
        db.commit()
        if config.MAX_PRICE_EUR and it["price"] and it["price"] > config.MAX_PRICE_EUR:
            continue
        if sent >= 15:
            continue                                  # защита от лавины
        send_listing(it, name)
        sent += 1
        time.sleep(1.2)
    log.info("%s: %d в выдаче, новых %d, отправлено %d",
             name, len(items), len(new), sent)


def run(debug=False, once=False):
    db = db_open()
    browser = Browser()
    try:
        while True:
            for s in config.SEARCHES:
                try:
                    check_search(browser, db, s, debug=debug)
                except Exception as e:
                    log.warning("%s: ошибка: %s", s["name"], e)
                time.sleep(random.uniform(3, 8))
            if debug or once:
                break
            wait = config.CHECK_EVERY_SECONDS + random.uniform(-30, 30)
            time.sleep(max(60, wait))
    finally:
        browser.close()


def show_chat_id():
    j = tg("getUpdates")
    chats = {}
    for u in j.get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        c = msg.get("chat")
        if c:
            chats[c["id"]] = c.get("username") or c.get("title") or c.get("first_name")
    if not chats:
        print("Не вижу сообщений. Напишите боту что-нибудь в Telegram и повторите.")
    for cid, nm in chats.items():
        print(f"chat id: {cid}   ({nm})")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--chat-id", action="store_true")
    p.add_argument("--test", action="store_true")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    if "ВСТАВЬТЕ" in config.TELEGRAM_TOKEN:
        sys.exit("Сначала впишите TELEGRAM_TOKEN в config.py")
    if a.chat_id:
        show_chat_id()
    elif a.test:
        send_text("🏠 Бот на связи. Как только появятся новые объявления — пришлю.")
        print("Отправлено. Проверьте Telegram.")
    else:
        if not a.debug and "ВСТАВЬТЕ" in str(config.TELEGRAM_CHAT_ID):
            sys.exit("Сначала впишите TELEGRAM_CHAT_ID в config.py (python bot.py --chat-id)")
        run(debug=a.debug, once=a.once)
