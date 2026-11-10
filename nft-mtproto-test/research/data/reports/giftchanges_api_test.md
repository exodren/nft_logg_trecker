# Проверка API GiftChanges (`api.changes.tg`)

Дата: 2026-11-09. Скрипт: `research/test_giftchanges_api.py`. Полные ответы: `research/data/giftchanges/`.
Запросы шли без авторизации, токенов и платных вызовов. Scanner, production-код и `final_report.md` не менялись.

## Вывод

API подходит как бесплатный справочник атрибутов коллекций: модели, фоны, символы, редкости, ассеты, события изменений, аукционы.
**Данных о владельцах, продавцах, текущих listings конкретных NFT и истории передач в документированном API нет.** Слаг и номер отдельного NFT он не принимает.

## Запросы

Все ответы получены без авторизации. В OpenAPI `security: []`, `securitySchemes` нет, все 31 путь только GET.

| Запрос | HTTP | Размер, байт | Содержимое |
|---|---|---|---|
| `/health` | 200 | 487 | статус, счётчики, heartbeat воркеров |
| `/gift/MoonPendant` | 200 | 99 561 | 50 моделей, 80 фонов, 161 символ, emoji |
| `/gift/ViceCream` | 200 | 132 941 | 50 моделей, 80 фонов, 300 символов, emoji |
| `/models/MoonPendant` | 200 | 10 171 | 50 моделей |
| `/changes` | 200 | 19 614 | 100 событий и курсор `next` |
| `/openapi.json` | 200 | 68 272 | OpenAPI 3.1.0 |
| `/backdrops/MoonPendant` | 200 | 27 513 | 80 фонов |
| `/symbols/MoonPendant` | 200 | 15 937 | 161 символ |
| `/total` | 200 | 115 | агрегаты каталога |
| `/auctions` | 200 | 1 429 | 4 аукциона |
| `/crafts` | 200 | 15 113 | крафтовые модели |
| `/llm` | 200 | 28 397 | справка в Markdown |
| `/gift/{gift_id}` × 14 | 200 | — | справочники для сопоставления |
| `/documents?ids=…` (25 id) | 200 | — | пакетное определение модели |

## Структура ответов

| Что проверяли | Результат | Поля |
|---|---|---|
| ID подарка | есть | `gift.id` (`"5998981470310368313"`), `giftId` в событиях и `/documents`; строка |
| Модели | есть | `name`, `telegramName`, `rarityPermille`, `rarity`, `originalRarityPermille`, `originalRarity`, `documentId`, `documentIds` |
| Backdrop | есть | `name`, `backdropId`, 4 цвета (число и `hex`), `customEmojiId`, редкости |
| Symbols | есть | `name`, редкости |
| Редкость | есть | промилле (`30`) и проценты (`3`), текущая и исходная |

### Примеры реальных ответов (усечены)

`/health`
```json
{"status":"ok","uptime":489700,"gifts":124,"models":7942,"symbols":26327,"workers":{"floor-prices":{"lastBeatAt":"2026-11-09T13:24:19.456Z","ageSeconds":8}}}
```

`/gift/MoonPendant`, блок `gift`
```json
{"name":"Moon Pendant","id":"5998981470310368313","customEmojiId":"5422709589193815905","releasedAt":1743282829,"upgradableAt":1754912460,"upgradable":true,"auction":false}
```

`/models/MoonPendant`, элемент
```json
{"name":"Silver Moon","rarityPermille":30,"originalRarityPermille":30,"documentId":"5199935223302749803","documentIds":["5199935223302749803"],"telegramName":"Silver Moon","rarity":3,"originalRarity":3}
```

`/symbols/MoonPendant`, элемент
```json
{"name":"Glasses","rarityPermille":6,"rarity":0.6,"originalRarityPermille":6,"originalRarity":0.6}
```

`/changes`, событие
```json
{"id":"1793604081370-0","gift":"Resistance Dog","giftId":"5832279504491381684","fromPermille":10,"toPermille":9,"type":"model.rarity_changed","model":"Lightpaw","at":"2026-11-02T07:21:21.362Z"}
```

`/documents?ids=…`, значение
```json
{"documentId":"5231323024690542422","current":true,"gift":"Clover Pin","giftId":"5960747083030856414","model":{"name":"Peacock","rarityPermille":30}}
```

## Сопоставление с NFT из MTProto

Выборка: 25 сохранённых карточек `StarGiftUnique` из `research/data/raw/unique_sample/`, 14 коллекций. Построчно: `research/data/giftchanges/mtproto_match.json`. Поля владельца в этот файл не попадают.

| MTProto | GiftChanges | Совпало |
|---|---|---|
| `StarGiftUnique.gift_id` | `/gift/{id}` | 14 из 14 коллекций |
| `StarGiftAttributeModel.document.id` | `documentIds` | 25 из 25 |
| тот же id, пакетно | `/documents?ids=` | 25 из 25 |
| имя модели | `telegramName` | 25 из 25 |
| редкость модели, промилле | `rarityPermille` | 25 из 25 |
| имя символа и редкость | `symbols[]` | 25 из 25 |
| имя фона и редкость | `backdrops[]` | 25 из 25 |
| `backdrop_id` | `backdropId` | 25 из 25 |

Не сопоставляются, потому что в API нет соответствующей сущности: `StarGiftUnique.id`, `slug` и `num` конкретного NFT, `owner_id`, `owner_name`, `owner_address`, `host_id`, `gift_address`, `resell_amount`.

Правило для модели: ключом брать `document.id`, а не имя, так как имена в коллекции бывают не уникальны (документация приводит Santa Hat и Jelly Bunny).

Ограничение: выборка мала. Карточки сняты 2026-11-08, а редкости могут меняться, поэтому совпадение подтверждает формат и текущие данные, но не их постоянство.

## Владельцы, продавцы, listings, история передач

Что просмотрено: все 31 путь, 13 параметров, 11 схем и 81 свойство OpenAPI, описания схем и эндпоинтов, полный текст `/llm`, ключи всех сохранённых ответов.

| Данные | Результат | Основание |
|---|---|---|
| Владелец NFT | нет | нет путей, параметров и свойств owner, holder, user, username, peer, wallet, address |
| Продавец | нет | нет seller; нет сущности отдельного NFT |
| Текущие listings | нет | нет путей и схем resale, listing, market |
| История передач | нет | нет transfer; перечень типов `ChangeEvent` ниже |
| Цены продаж NFT | нет | цены есть только в аукционах, в Stars |
| Floor-цены | не опубликованы | см. ниже |

Поля и названия, которые можно принять за нужные данные, но это не они:

- `listedCount`, `fragmentListedCount` в `/auctions` и `/auction/{gift}/rounds`: агрегированные числа Telegram по завершённому аукциону (UFC Strike: 7289 и 553). Конкретных NFT и продавцов за ними нет.
- `/auction/{gift}/rounds`: цены и ставки по раундам, без участников.
- `slug` в аукционах: слаг аукциона или коллекции (`TrojanHorse`), а не NFT вроде `IonGem-152`.
- `/changes`: 14 типов событий, все про каталог (`model.*`, `symbol.*`, `backdrop.*`, `gift.*`, `crafts.added`). Это изменения атрибутов и не история владения.
- `workers.floor-prices` в `/health`: только heartbeat фонового воркера. Документированного эндпоинта с floor-ценами нет.

Что осталось непроверенным:

- недокументированные пути не перебирались;
- `cdn.changes.tg` не запрашивался;
- `/gift/{gift}?model=&backdrop=&symbol=` по OpenAPI определяет комбинацию атрибутов; вызов не проверялся, параметров владельца в нём нет.

## Что API закрывает в NFT-трекере

- Обогащение карточки: редкость модели, символа и фона, цвета.
- Проверка атрибутов NFT по `document.id`, в том числе перерисованных моделей через `documentIds`.
- Преобразование `gift_id` в название коллекции и обратно (`/ids`, `/names`).
- Изображения и анимации моделей, символов и исходных подарков (форматы заявлены в документации; скачивание не проверялось).
- Оповещения о новых подарках, моделях и изменениях редкости через `/changes?since=` (эндпоинт в бете, автор предупреждает о возможном удалении).
- Статистика аукционов и крафтов.

## Что API не закрывает

- Определение владельца или продавца NFT, его ID и username.
- Поиск NFT по слагу и номеру, статус конкретного экземпляра.
- Обнаружение и мониторинг выставленных на продажу NFT, их цены и floor.
- История передач и продаж конкретного NFT, on-chain данные.

Для этих задач по-прежнему нужны MTProto и другие источники, в уже согласованных рамках.

## Условия использования

- В OpenAPI лицензия указана как `free to use with credit`.
- При интеграции в пользовательский бот нужно добавить видимую строку `Thanks to @GiftChanges for this API` в `/about` или `/help`. Сейчас API к боту не подключён, поэтому строка не добавлялась.
- `api_SEE.TG.txt` распространяет требование на любые скрипты и утверждает, что README неопубликованного проекта или комментарий не считаются. Тестовый скрипт конечным пользователям не поставляется, и по заданию условие применяется при интеграции.
- Несмотря на имя файла, в `api_SEE.TG.txt` документация GiftChanges, а не See.TG.
