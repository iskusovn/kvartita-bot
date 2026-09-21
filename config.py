# ============================================================
#  Настройки бота. Меняйте только этот файл.
# ============================================================

import os

# 1-2) Токен бота и chat id. В облаке они берутся из секретов GitHub
#      (Settings → Secrets and variables → Actions), здесь их писать НЕ нужно.
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# 3) Пауза между проверками — только для запуска на своём компьютере.
#    В облаке расписание задаётся в файле .github/workflows/check.yml
CHECK_EVERY_SECONDS = 180

# 4) Страховка: объявления дороже этой суммы не присылать
#    (на случай, если фильтр на сайте не сработал). None = не фильтровать.
MAX_PRICE_EUR = 600

# 5) Поиски. Чтобы добавить новый: настройте фильтры на сайте,
#    скопируйте ссылку из адресной строки и добавьте блок.
#    site: "halooglasi", "cityexpert", "nekretnine" или "kupujemprodajem".
#    Важно: сортировка на сайте должна быть «сначала новые».
SEARCHES = [
    {
        "name": "Halo Oglasi · стан до 600€",
        "site": "halooglasi",
        "url": "https://www.halooglasi.com/nekretnine/izdavanje-stanova/beograd?cena_d_to=600&cena_d_unit=4",
    },
    {
        "name": "Halo Oglasi · кућа до 600€",
        "site": "halooglasi",
        "url": "https://www.halooglasi.com/nekretnine/izdavanje-kuca/beograd?cena_d_to=600&cena_d_unit=4",
    },
    {
        "name": "CityExpert · стан до 600€",
        "site": "cityexpert",
        "url": "https://cityexpert.rs/izdavanje-nekretnina/beograd?ptId=1&maxPrice=600",
    },
    {
        "name": "Nekretnine.rs · до 600€",
        "site": "nekretnine",
        "url": "https://www.nekretnine.rs/izdavanje-stambenih-nekretnina/beograd/?prezzoMassimo=600#geohash-srywcgy7",
    },
    {
        "name": "KupujemProdajem · стан до 600€",
        "site": "kupujemprodajem",
        "url": "https://www.kupujemprodajem.com/nekretnine-izdavanje/stanovi/pretraga?categoryId=2850&groupId=2851&priceTo=600&currency=eur&realEstateLocation=548",
    },
]
