# Отчёт: Фаза 1 — Фундамент (T1.1–T1.4)

Дата: 2026-11-10.

## Сделано
- `app/config.py`: 12 переменных §5, типы и валидация, `ConfigError` с понятным текстом, проверка по компонентам (`require("bot")`), `secrets()` для маскирования. По умолчанию `DAILY_API_BUDGET_TON=0` — только бесплатная квота, платные запросы без явного бюджета невозможны.
- `app/utils/log.py`: консоль + файл `logs/app.log` с ротацией (5 × 5 МБ), категории `app.mtproto`, `app.api`, `app.database`, `app.bot`, маскирование секретов из конфига, токенов вида `id:secret` и `?token=` — включая трейсбеки.
- `requirements.txt`, `.env.example`, `pytest.ini`, `.gitignore` (+ `logs/`, `data/`, `backups/`, `*.db`).
- Зависимости: aiogram 3.31.0, SQLAlchemy 2.1.8 (asyncio, greenlet), Alembic 1.20.1, aiosqlite 0.22.1, pytest-asyncio 1.4.1. Telethon и aiohttp не менялись.
- БД: 10 таблиц §5 (`app/database/models.py`), миграция Alembic `47bde40b0d81_initial_schema`, `migrate()` при старте.
  - `Money`: NUMERIC(38,9) на PostgreSQL, текст на SQLite (NUMERIC в SQLite — float и терял бы нанотоны). На SQLite деньги сравниваются в Python.
  - `UTCDateTime`: только aware-время, SQLite читается как UTC.
  - Telegram ID — BIGINT. Частичные уникальные индексы: один активный листинг на NFT, одна активная задача очереди на ключ.
  - SQLite: WAL, `foreign_keys=ON`, `busy_timeout=5000`.

## Тесты
`pytest tests`: config (11), logging (3), database (8, включая `alembic check` — миграция совпадает с моделями). Все прошли.

## Проблемы
- `SQLAlchemy[asyncio]` нужен для greenlet — исправлено в `requirements.txt`.
- Alembic `fileConfig` по умолчанию отключает логгеры приложения — в `env.py` логирование ini применяется только для CLI.

## Следующий этап
Фаза 2 — Parser.
