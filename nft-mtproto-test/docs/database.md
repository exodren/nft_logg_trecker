# База данных

SQLAlchemy 2 (async) + Alembic. MVP — SQLite (`data/tracker.db`, WAL, `foreign_keys=ON`), переход на PostgreSQL — сменой `DATABASE_URL`. Модели: `app/database/models.py`, миграции: `app/database/migrations/versions/`.

Типы:
- Telegram ID — `BIGINT` (значения больше 2^53).
- Деньги — `Money`: `NUMERIC(38,9)` на PostgreSQL, текст на SQLite (NUMERIC в SQLite хранится как float и терял бы нанотоны). На SQLite деньги сравниваются в Python.
- Время — `UTCDateTime`, только UTC с таймзоной.

| Таблица | Назначение | Ключи и ограничения |
|---|---|---|
| `users` | пользователи бота | `telegram_id` UNIQUE |
| `gifts` | NFT (коллекция + номер), атрибуты, редкость, `raw_data` | `telegram_unique_id` UNIQUE, `slug` UNIQUE, (`collection`, `number`) UNIQUE; индексы collection, number, model, backdrop, pattern, gift_type_id |
| `listings` | эпизод продажи: цена в валюте продавца (+ Stars и TON), `seller_name` (только текст), состояние ACTIVE/INACTIVE/UNKNOWN, `listing_date` (= время обнаружения), `owner_lookup_status` (§20) | `listing_identity` UNIQUE (`slug:эпизод`); частичный UNIQUE: один активный листинг на NFT |
| `owners` | владелец **с username** (Telegram ID — дополнительно, если есть). Владельцы без username, скрытые и неизвестные сюда не попадают | `telegram_id` UNIQUE (NULL допустим); username может меняться |
| `owner_lookup_cache` | результат каждого запроса владельца по NFT: статус `FOUND` / `NO_USERNAME` / `HIDDEN` / `NOT_FOUND` / `UNAVAILABLE` / `ERROR`, приватность, стоимость, `expires_at`, `last_checked_at`, `raw_data`. Финальные статусы живут `API_CACHE_TTL` — повторной оплаты нет | `gift_id` UNIQUE |
| `api_usage` | учёт Kartoshka API по UTC-суткам: запросы, успешные, ошибки, бесплатные, платные, TON, квота, баланс, причина остановки | (`provider`, `day`) UNIQUE |
| `notifications` | одно уведомление на пользователя на событие, статус доставки | (`user_id`, `event_key`) UNIQUE |
| `scan_state` | прогресс сканера по типу подарка | `gift_type_id` UNIQUE |
| `processing_queue` | очередь задач: тип, приоритет, попытки, `next_attempt_at`, payload | частичный UNIQUE `dedup_key` среди pending/processing/retry |
| `user_filters` | этап 1: коллекции, модели, фоны, паттерны, цена (+валюта); этап 2: `require_username`, `require_telegram_id`, `owner_usernames`, `owner_telegram_ids` (сравниваются с владельцем найденного NFT, поиск по ним не выполняется); флаги уведомлений | FK `user_id` CASCADE |

Удаление пользователя удаляет его фильтры и уведомления. История листингов не удаляется: закрытый эпизод получает `INACTIVE` и `removed_at`.
