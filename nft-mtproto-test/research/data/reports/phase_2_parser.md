# Отчёт: Фаза 2 — Parser (T2.1–T2.2)

Дата: 2026-11-10.

## Сделано
`app/mtproto/parser.py` — чистые функции без сети, перенос логики `research/analyzers/gift_parser.py`:
- `parse_listing(obj, seen_at)` принимает TL-объект Telethon или dict, возвращает `ParsedListing` (frozen dataclass).
- `parse_page(result, seen_at)` — страница `payments.ResaleStarGifts`; плохой элемент попадает в `errors`, страница не теряется.
- Цены `Decimal`: Stars = `amount + nanos/1e9`, TON = нанотоны / 1e9. `price`/`currency` — в валюте продавца (`resale_ton_only` → TON, иначе XTR).
- `split_slug`: `rsplit` по последнему дефису, проверка числа и совпадения с `num`.
- `raw_data` — полный JSON-безопасный исходник.

Расхождения с полями §6 (из аудита):
- `listing_date` = момент обнаружения: в `StarGiftUnique` нет даты листинга.
- `listing_identity`: парсер отдаёт основу (`slug`); эпизод знает только БД, `'{slug}:{episode}'` собирает сервис листингов (T3.3).
- `owner_name` сохраняется как `seller_name` — только текст, не username.

## Тесты
`tests/test_parser.py` (20): slug (корректные, с дефисами в названии, некорректные), реальный листинг IonGem-3185 (8755 XTR / 123.24 TON), большие ID, TON-only, точность TON, отсутствующие цена и атрибуты, неизвестные атрибуты и типы цен, некорректные значения, страница с плохим элементом, все 2250 листингов дампов без ошибок. Фикстуры: `tests/fixtures/resale/` (копии двух страниц, оригиналы на месте).

Итог: `pytest tests` — 43 passed.

## Следующий этап
Фаза 3 — MTProto Scanner (T3.1–T3.4).
