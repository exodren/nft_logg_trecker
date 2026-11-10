# Telegram NFT Tracker & Owner Bot

## 1. Что делает приложение

1. **Сканер MTProto** опрашивает `payments.getResaleStarGifts` по всем типам подарков с листингами и сохраняет каждый NFT и листинг в БД.
2. **Этап 1 фильтрации**: если новый листинг подходит хотя бы одному пользователю по коллекции, цене или атрибутам, он ставится в очередь на получение владельца. Если не подходит никому — сохраняется, но платный запрос не делается.
3. **Kartoshka API** (`GET /v1/gift/{slug}`, официальный API see.tg) отдаёт данные владельца. Действуют кэш (`API_CACHE_TTL`), бесплатная квота и дневной бюджет.
4. **Этап 2**: условия на владельца (есть username, есть Telegram ID, конкретный `@username` или ID) проверяются у владельца найденного NFT. Владелец учитывается **только с username**: Telegram ID без username не сохраняется в `owners`, не показывается в карточке и не даёт совпадений (результат `NO_USERNAME` кэшируется, чтобы NFT не оплачивался повторно). Профиль по ID не ищется, приватность не обходится.
5. **Бот** рассылает карточки, даёт поиск по локальной БД, статистику, настройки и админ-панель.

Компоненты (сканер, воркеры очереди, уведомления, бот, резервные копии) работают как независимые asyncio-задачи одного процесса: остановка или перезапуск одного не останавливает остальные.

## 2. Установка

Python 3.12.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate — Linux/macOS
pip install -r requirements.txt
```

## 3. Настройка `.env`

```bash
copy .env.example .env          # cp .env.example .env
```

| Переменная | Назначение |
|---|---|
| `BOT_TOKEN` | токен бота (обязательно) |
| `API_ID`, `API_HASH` | MTProto, https://my.telegram.org (без них бот работает только с уже сохранёнными данными) |
| `TELEGRAM_SESSION` | файл сессии приложения (по умолчанию `sessions/app.session`) |
| `KARTOSHKA_API_TOKEN` | токен Kartoshka API; пусто — владельцы не запрашиваются |
| `DATABASE_URL` | по умолчанию SQLite `data/tracker.db`; PostgreSQL: `postgresql+asyncpg://…` (+ `pip install asyncpg`) |
| `ADMIN_TELEGRAM_IDS` | Telegram ID администраторов через запятую |
| `LOG_LEVEL` | DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `MAX_CONCURRENT_API_REQUESTS` | параллельные запросы к Kartoshka и число воркеров очереди |
| `DAILY_API_BUDGET_TON` | дневной бюджет; **0 — только бесплатная квота (100 запросов/сутки)** |
| `API_CACHE_TTL` | сколько секунд данные владельца считаются свежими |
| `LISTING_SCAN_INTERVAL` | пауза между циклами сканирования, секунды |
| `LOG_GROUP_ID` | **единственная** Telegram-группа для служебных логов (числовой ID группы/супергруппы, `-100…`; бот должен быть участником). Пусто или некорректно — логи в Telegram не отправляются, `logs/app.log` работает как обычно |

Секреты хранятся только в `.env` (он в `.gitignore`) и маскируются в логах.

## 4. Создание Telegram-бота

1. В [@BotFather](https://t.me/BotFather): `/newbot`, скопировать токен в `BOT_TOKEN`.
2. Описание бота (`/setdescription`) должно содержать строку «Данные: see.tg — https://see.tg» — это правило Kartoshka API.
3. Свой Telegram ID (например, через @userinfobot) — в `ADMIN_TELEGRAM_IDS`.

## 5. Настройка MTProto

1. `API_ID` и `API_HASH` с https://my.telegram.org.
2. Один раз войти: `python test_gift.py` (создаст `sessions/mtproto_test.session`).
3. При первом запуске приложение скопирует её в `sessions/app.session` и работает с копией (одну SQLite-сессию нельзя открыть из двух процессов). Интерактивного входа в самом приложении нет.

Используются только методы чтения: `GetStarGifts`, `GetResaleStarGifts`, `GetUniqueStarGift`.

## 6. Подключение Kartoshka API

1. Токен: мини-апп [@seetgbot](https://t.me/seetgbot) → Полезное → API → создать приложение (нужен Telegram Premium; название и ссылка — реальные). Записать в `KARTOSHKA_API_TOKEN`.
2. **Подтвердить схему ответа** (один раз, в пределах бесплатной квоты):
   ```bash
   python -m scripts.capture_kartoshka_fixtures --allow-paid <slug-с-username> <slug-без-username> <slug-скрытого> Nope-999999999
   ```
   Без `--allow-paid` скрипт работает только в бесплатной квоте и останавливается на первом платном ответе. Ответы попадут в `tests/fixtures/kartoshka/`. По ним пишется проверенный маппер полей владельца (`app/providers/kartoshka.py`, `OwnerMapper`). **До этого провайдер не вызывается** (`ready=False`): поля ответа не угадываются.
3. Пополнение баланса — в мини-аппе. Платные запросы начинаются только при `DAILY_API_BUDGET_TON > 0`.

Правила сервиса: https://kartoshka.free/rules.js. Методы `/who` и `/owners/search` не используются. Подробности: `research/data/reports/kartoshka_api_integration.md`.

## 7. Запуск

```bash
python -m app
```

При старте применяются миграции (Alembic), восстанавливаются прерванные задачи очереди и в лог выводится статус: версия, БД, MTProto, бот, Kartoshka, очередь. Остановка — Ctrl+C (компоненты закрываются корректно). Логи — текстовый файл `logs/app.log`, каждые 6 часов (UTC) он сохраняется как `logs/app.log.ГГГГ-ММ-ДД_ЧЧ` и начинается новый; хранятся 28 файлов (7 дней). Скрытые владельцы в Telegram не упоминаются — запись «🔒 Владелец скрыл профиль» с slug NFT есть только в логе. Резервная копия SQLite — раз в сутки в `backups/` (7 последних).

Миграции вручную: `alembic upgrade head`.

### Три независимых потока

| Поток | Получатель | Где |
|---|---|---|
| NFT-уведомления (новые листинги, владельцы) | подписчики в личных сообщениях — по их подписке и фильтрам | `app/services/notifications.py` |
| Служебные логи (WARNING+: сбои сканера, бюджет и ошибки Kartoshka, ошибки доставки) | только группа `LOG_GROUP_ID` | `app/services/log_sender.py` |
| Локальный лог (всё) | `logs/app.log`, ротация 6 ч | `app/utils/log.py` |

`LOG_GROUP_ID` касается только второго потока: он не ограничивает и не перенаправляет NFT-уведомления, а NFT-уведомления не отправляются через Log Sender. Сбой одного потока не влияет на другие.

### Служебные логи в Telegram

`app/services/log_sender.py`. Записи уровня WARNING и выше дублируются в одну группу — `LOG_GROUP_ID`:
- получатель берётся только из `LOG_GROUP_ID`; при старте он проверяется (число, `getChat` → тип `group` или `supergroup`). Личный чат, канал, пустое или некорректное значение — отправка выключена, ошибка пишется в `logs/app.log`, другая группа не подбирается;
- все отправки проходят через одну проверку получателя; ни команды, ни настройки, ни входящие сообщения не могут его изменить;
- ошибки самой отправки пишутся только в `logs/app.log` — повторной отправки в Telegram и рекурсии нет;
- секреты маскируются, одинаковые сообщения за 60 с не повторяются, строки о скрытых владельцах в группу не попадают;
- уведомления пользователям о NFT — отдельный механизм (`notifications.py`) и в группу логов не идут.

## 8. Тесты

```bash
pytest
```

Тесты не используют реальные сессии, токены и сеть: Telegram и Kartoshka заменены фейками и локальным HTTP-сервером.

## 9. Статистика расходов

Админ: `/admin` в боте — запросы за сегодня (UTC), успешные, ошибки, бесплатные/платные, потрачено TON, остаток бюджета, средняя стоимость, остаток бесплатной квоты и баланс. При 80 % бюджета, исчерпании бюджета, 402 и 401 пишется предупреждение (WARNING): в `logs/app.log` и в группу `LOG_GROUP_ID`. Личных служебных сообщений администраторам нет. После пополнения: `/resume_api`.

## Команды бота

| Команда | Кто | Что делает |
|---|---|---|
| `/start` | все | регистрация и меню (новые листинги, мои совпадения, поиск, настройки, статистика, помощь) |
| `/search <запрос>` | все | поиск по локальной БД: `MoonPendant-71710`, `Moon Pendant 71710`, `IonGem`, `71710`; любой текст без `/` тоже поиск |
| `/stats` | все | NFT, активные и новые листинги, обработанные подарки, найденные и скрытые владельцы, ошибки |
| `/settings` | все | фильтры и переключатели уведомлений (по умолчанию уведомления выключены) |
| `/filter collections …` / `models` / `backdrops` / `patterns` / `price 100 5000 XTR` / `price off` / `reset` | все | фильтры NFT (этап A); после изменения сохранённые листинги проверяются заново (кэш и бюджет учитываются) |
| `/filter owner @username, 8556771897` | все | условие на владельца (этап B): проверяется у владельца каждого найденного NFT; NFT по username или ID не ищутся |
| `/help` | все | справка |
| `/admin` | админ | состояние MTProto, сканера, Kartoshka, очереди, пользователей, расходов, компонентов |
| `/errors`, `/retry_failed`, `/retry_deferred`, `/resume_api`, `/restart <scanner\|workers\|notifications\|log_sender\|bot\|backup>` | админ | обслуживание |

## Структура

```
app/            config, main (точка входа), supervisor
  mtproto/      client (read-only Telethon), parser, scanner
  providers/    base (контракт OwnerData), kartoshka, mock
  services/     listings, pipeline (этапы 1–2), filters, queue, enrichment, cache, api_usage,
                notifications, queries, backup
  database/     models, types (Money, UTCDateTime), session, migrations/
  bot/          factory, handlers, admin, formatters, keyboards, middlewares
tests/          pytest; fixtures/
scripts/        capture_kartoshka_fixtures.py
research/       исследования MTProto (не изменялись)
docs/           database.md, implementation.md (что реализовано), manual_test_plan.md (ручные тесты),
                session_2026-11-10.md (журнал сессии)
```
