# ============================================================
#  Настройки бота. Меняйте только этот файл.
# ============================================================

# 1) Токен от @BotFather (выглядит как 123456789:AAH...)
TELEGRAM_TOKEN = "ВСТАВЬТЕ_ТОКЕН_СЮДА"

# 2) Ваш chat id. Узнать: напишите боту любое сообщение,
#    потом запустите:  python bot.py --chat-id
TELEGRAM_CHAT_ID = "ВСТАВЬТЕ_CHAT_ID_СЮДА"

# 3) Как часто проверять сайты (в секундах). 180 = 3 минуты.
#    Реже 2 минут ставить не стоит: сайты могут начать блокировать.
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
