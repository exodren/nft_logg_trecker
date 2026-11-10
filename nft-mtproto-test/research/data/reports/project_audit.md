# Аудит проекта (A.1)

Дата: 2026-11-10. Источник требований: `актуальный промпт.md`. Код при аудите не менялся.

## 1. Структура

```
ticket_NFT_bot/
├── актуальный промпт.md      требования (главный документ)
├── промпт.md                 старая версия, исторический контекст
├── TASKS.md                  план
├── api_SEE.TG.txt            несмотря на имя — документация GiftChanges API
├── текст.txt, vault/         заметки
└── nft-mtproto-test/         ← корень приложения (.env, sessions/, research/)
    ├── .env                  API_ID, API_HASH, PHONE, GIFT_SLUG (значения не читались)
    ├── .gitignore            .env, *.session, sessions/, .venv/, research/data/
    ├── sessions/mtproto_test.session   авторизованная сессия (создана test_gift.py)
    ├── test_gift.py          интерактивный вход (client.start(phone=...))
    ├── gram_identity.py, parse_history.py   on-chain утилиты (TON-адрес → .t.me)
    └── research/
        ├── config.py         настройки, .env, лимиты запросов
        ├── mtproto_client.py read-only обёртка Telethon
        ├── analyzers/        gift_parser, event_parser, peer_resolver, hypothesis_validator
        ├── scripts/          17 исследовательских скриптов
        ├── tests/            4 файла, 23 теста
        └── data/             raw (19 МБ, 192 файла), parsed, reports, giftchanges
```

Git-репозитория нет. `graphify-out/` нет.

## 2. Что работает (проверено)

| Компонент | Где | Состояние |
|---|---|---|
| Авторизация Telethon | `test_gift.py`, `sessions/mtproto_test.session` | работает, сессия авторизована |
| Read-only клиент | `research/mtproto_client.py` → `Api`, `open_api` | allowlist методов, задержка 1,5 с, FloodWait (с потолком 120 с), retry сети с backoff, таймаут, отказ без интерактивного входа |
| Листинги | `research/scripts/test_resale.py` | `GetResaleStarGiftsRequest(gift_id, offset, limit)`, постраничное сохранение, возобновление |
| Каталог типов | `test_star_gifts.py` → `fetch_gift_types` | `GetStarGiftsRequest(hash=0)` |
| Парсер | `research/analyzers/gift_parser.py` | чистые функции над dict: цены, атрибуты, `price_key` |
| Тесты | `research/tests/` | **23 passed** (`pytest research/tests`, 2,0 с) |
| Дампы | `research/data/raw/resale/` | 45 страниц, 2250 `StarGiftUnique` |

Telethon 1.45.0, Python 3.12.10. Реальные сигнатуры (проверены в установленной версии):
- `GetResaleStarGiftsRequest(gift_id, offset, limit, sort_by_price=None, sort_by_num=None, for_craft=None, stars_only=None, attributes_hash=None, attributes=None)`
- `payments.ResaleStarGifts(count, gifts, chats, users, next_offset=None, attributes=None, attributes_hash=None, counters=None)`
- `StarGiftUnique(id, gift_id, title, slug, num, attributes, availability_issued, availability_total, …, burned, crafted, owner_id, owner_name, owner_address, gift_address, resell_amount, released_by, value_amount, value_currency, value_usd_amount, host_id, …)`

## 3. Установленные факты (из `final_report.md`, не перепроверяются)

- Листинги получаются только по `gift_id`: 151 тип, 124 с листингами, ~827 тыс. листингов. Полный обход ≈ 8 270 запросов (~3,4 ч), опрос первой страницы всех типов ≈ 124 запроса (~3 мин при 1,5 с).
- Лимит страницы 100 (101 молча обрезается). Пагинация стабильна.
- **Мусорный `offset` → 0 записей и непустой `next_offset`**: без защиты вечный цикл.
- Неизвестный `gift_id` → `STARGIFT_INVALID`; тип без листингов → `count = 0`.
- Порядок по умолчанию похож на «свежие сверху», не доказан. `count` не индикатор новых листингов.
- `owner_id`, `host_id`, `owner_address` пусты в 2250/2250. `owner_name` — отображаемое имя, не username. `result.users` — участники исходного дарения, не продавцы.
- Цену сравнивать в валюте продавца (`price_key`): вторая валюта — плавающая конвертация.
- Исчезновение со страницы ничего не доказывает; продажу от снятия не отличить.

## 4. Недостающие компоненты

Приложения нет: нет `app/`, конфигурации по §5, логирования с категориями, БД и миграций, сканера как сервиса, очереди, провайдера Kartoshka, бота, уведомлений, админки, `requirements.txt` с нужными зависимостями, README. Не установлены: aiogram, SQLAlchemy, Alembic, aiosqlite, pytest-asyncio.

## 5. Технический долг и найденные ошибки

| # | Проблема | Влияние | Решение |
|---|---|---|---|
| 1 | `gift_parser.parse_prices` делит нанотоны на `1e9` во `float` | потеря точности TON, нарушает §5 | в `app/` — `Decimal`; research не трогаем |
| 2 | **В MTProto нет даты листинга** (поля нет в `StarGiftUnique`) | §6 требует `listing_date` | `listing_date` = время первого обнаружения (`first_seen_at`), задокументировать |
| 3 | **Нет стабильного ID листинга** | §7 T3.3 требует `listing_identity` | составной ключ, см. п. 7 |
| 4 | `Api` сериализует все запросы с фиксированной задержкой | цикл по 124 типам ~3 мин | для MVP допустимо; интервал в `LISTING_SCAN_INTERVAL` |
| 5 | Одна SQLite-сессия Telethon не может использоваться двумя процессами | конфликт с research-скриптами | приложение работает с собственной копией сессии (`TELEGRAM_SESSION`), как `research` |
| 6 | `.gitignore` исключает `research/data/` | тесты на дампах не переносимы | небольшой набор дампов копируется в `tests/fixtures/` (оригиналы не трогаются) |
| 7 | В `.env` нет `BOT_TOKEN`, `KARTOSHKA_API_TOKEN`, `DATABASE_URL`, `ADMIN_TELEGRAM_IDS`, `TELEGRAM_SESSION` | бот и провайдер не запустятся | `.env.example`, понятная ошибка при старте |
| 8 | `api_SEE.TG.txt` содержит документацию GiftChanges | путаница | не трогать, отмечено здесь |
| 9 | Промпт ссылается на `prompt.md`, в проекте `промпт.md` | — | старый файл — исторический контекст |

## 6. Что переиспользуется

- Логика `research/mtproto_client.Api` (allowlist, FloodWait-потолок, retry, таймаут, отказ без логина) → переносится в `app/mtproto/client.py`. Прямой импорт не используется: `research/config.py` загружает собственный `.env` и пути к сессии.
- `gift_parser.attribute`, `price_key` → логика переносится в `app/mtproto/parser.py` с `Decimal`.
- Защита от вечного цикла по `offset`, лимит 100 на страницу — из `test_pagination`.
- Сессия `sessions/mtproto_test.session` — источник копии для приложения; `test_gift.py` остаётся способом входа.
- Дампы `research/data/raw/resale/*.json` — фикстуры парсера и сканера.

## 7. Технические решения для MVP

- **Корень приложения:** `nft-mtproto-test/` (там `.env`, сессии, research). Новый код — `app/`, тесты — `tests/`. Структура `research/` не меняется.
- **Обнаружение:** опрос первых страниц по типам с листингами, `LISTING_SCAN_INTERVAL` между циклами; число страниц на тип — настройка.
- **`listing_identity`:** `"{slug}:{n}"`, где `n` — номер эпизода продажи этого NFT. Новый эпизод начинается, если NFT снова появился после статуса `LISTING_INACTIVE`, или если сменился `owner_name` (другой продавец). Ограничение: снятие и повторное выставление тем же продавцом между двумя сканами неотличимо от продолжения листинга.
- **`LISTING_INACTIVE`** ставится только когда тип просмотрен полностью (дошли до пустого `next_offset`), а листинга нет. Иначе листинг, не встречавшийся дольше порога, получает `UNKNOWN_STATE`. Проданным не объявляется никогда.
- **Цена:** `price` и `currency` — в валюте продавца (`resale_ton_only` → TON, иначе Stars); обе суммы хранятся `Decimal`. `PRICE_CHANGED` — по `price_key`.
- **БД:** SQLAlchemy 2 async + aiosqlite, Alembic. Переход на PostgreSQL — сменой `DATABASE_URL`.

## 8. Зависимости

Установлено: Telethon 1.45.0, aiohttp 3.14.5, python-dotenv 1.2.4, pytest 9.2.0.
Добавить (T1.3): aiogram 3.x, SQLAlchemy 2.x, Alembic, aiosqlite, pytest-asyncio. Больше ничего.

## 9. Сводка (§19 п. 7)

- **Готово:** авторизация и сессия, read-only MTProto-доступ, получение листингов, парсер (float), 23 теста, 2250 листингов в дампах, исследование владельцев завершено.
- **Отсутствует:** всё приложение (см. п. 4).
- **Переиспользуется:** п. 6.
- **Требует исправлений:** float TON, дата листинга, ID листинга, общая сессия, переменные `.env` (п. 5).
- **Следующий этап:** K4.1 (отчёт по Kartoshka), затем T1.1–T1.3.
