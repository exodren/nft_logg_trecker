"""HTML texts for Telegram. Every user-controlled string goes through html.escape; links are built only
from a validated slug."""
from __future__ import annotations

import re
from decimal import Decimal
from html import escape

from app.providers.base import OwnerStatus
from app.services.api_usage import UsageReport
from app.services.queries import Card, Stats

SLUG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d{1,9}$")
SOURCE_LINE = 'Данные о владельце: <a href="https://see.tg">see.tg</a>'
APP_TITLE = "🎁 <b>NFT Tracker</b>"


def nft_url(slug: str) -> str | None:
    return f"https://t.me/nft/{slug}" if SLUG_RE.match(slug or "") else None


def money(amount: Decimal | None, currency: str | None) -> str:
    if amount is None or currency is None:
        return "Цена не определена"
    if currency == "TON":
        return f"{amount.normalize():f} TON"
    return f"{int(amount):,}".replace(",", " ") + " ⭐" if amount == amount.to_integral() else f"{amount:f} ⭐"


def owner_lines(c: Card) -> list[str]:
    """Owner block only for an owner with a username. Hidden, ID-only, unknown or unavailable owners
    show nothing in Telegram; the hidden status goes to the log (HIDDEN_OWNER_TEXT in app/providers/base.py)."""
    o = c.owner
    if o is not None and o.status is OwnerStatus.FOUND and o.username:
        lines = [f"👤 Владелец: @{escape(o.username)}"]
        if o.telegram_id is not None:
            lines.append(f"🆔 Telegram ID: <code>{o.telegram_id}</code>")
        return lines
    return []


def card_text(c: Card) -> str:
    g, l = c.gift, c.listing
    lines = [f"🎁 <b>{escape(g.title or g.collection)} #{g.number}</b>", "",
             f"Модель: {escape(g.model or '—')}", f"Фон: {escape(g.backdrop or '—')}",
             f"Паттерн: {escape(g.pattern or '—')}", "",
             f"💰 {money(l.price, l.currency) if l else 'Цена не определена'}"]
    if l is not None and not l.is_active:
        lines.append("⏸ Сейчас не в продаже")
    owner = owner_lines(c)
    if owner:
        lines += [""] + owner
    if l is not None:
        lines.append(f"🕒 Обнаружен: {l.first_seen_at:%d.%m %H:%M} UTC")
    url = nft_url(g.slug)
    if url:
        lines.append(f'<a href="{url}">Открыть NFT</a>')
    if owner and c.owner.provider == "kartoshka":  # credit see.tg whenever its data is shown
        lines += ["", SOURCE_LINE]
    return "\n".join(lines)


def short_line(c: Card) -> str:
    g, l = c.gift, c.listing
    owner = ""
    if c.owner is not None and c.owner.status is OwnerStatus.FOUND and c.owner.username:
        owner = f" · @{escape(c.owner.username)}"
    price = money(l.price, l.currency) if l else "Цена не определена"
    return f"<b>{escape(g.title or g.collection)} #{g.number}</b> — {price}{owner}"


START = (f"{APP_TITLE}\n\nОтслеживаю новые листинги Telegram NFT Gifts и показываю доступные публичные данные "
         "владельца.\n\nВыберите действие:")

HELP = (
    f"{APP_TITLE}\n\n"
    "<b>Команды</b>\n"
    "/start — главное меню\n/search &lt;запрос&gt; — поиск: <code>MoonPendant-71710</code>, "
    "<code>Moon Pendant 71710</code>, <code>IonGem</code>, <code>71710</code>\n"
    "/stats — статистика\n/settings — фильтры и уведомления\n/help — эта справка\n\n"
    "<b>Фильтры</b>\n"
    "/filter collections IonGem, MoonPendant\n/filter models Garnet\n/filter backdrops Indigo Dye\n"
    "/filter patterns Potion\n/filter price 100 5000 XTR  (или TON)\n/filter price off\n"
    "/filter owner @username, 8556771897\n/filter reset\n"
    "Пустое значение снимает ограничение.\n\n"
    "<b>Владельцы</b>\nБот не ищет NFT по username или ID. Он находит новые листинги, и для тех, что подходят "
    "по коллекции, цене и атрибутам, узнаёт владельца по самому NFT. Условия владельца (username, Telegram ID, "
    "конкретный владелец) проверяются после этого и срабатывают только для владельца с username: "
    "Telegram ID без username не используется. Скрытые профили не раскрываются.\n\n" + SOURCE_LINE)


def stats_text(st: Stats) -> str:
    return ("📊 <b>Статистика</b>\n\n"
            f"NFT в базе: {st.gifts}\nАктивных листингов: {st.active_listings}\n"
            f"Новых листингов за 24 ч: {st.new_listings_24h}\nОбработано подарков: {st.processed_gifts}\n"
            f"Владельцев с username: {st.owners_found}\nВладельцев без username: {st.owners_no_username}\n"
            f"Скрытых владельцев: {st.owners_hidden}\n"
            f"Ошибок обработки: {st.processing_errors}")


def usage_text(r: UsageReport) -> str:
    avg = f"{r.average_cost_ton:f}" if r.average_cost_ton is not None else "—"
    stopped = f"\n⛔ Платные запросы остановлены: {escape(r.stopped_reason)}" if r.stopped_reason else ""
    return (f"<b>Kartoshka API — {r.day:%d.%m.%Y} UTC</b>\n"
            f"Запросов: {r.requests} (успешных {r.success}, ошибок {r.errors})\n"
            f"Бесплатных: {r.free}, платных: {r.paid}\nПотрачено: {r.spent_ton:f} TON\n"
            f"Бюджет: {r.budget_ton:f} TON, остаток {r.remaining_budget_ton:f} TON\n"
            f"Средняя стоимость: {avg} TON\nБесплатных осталось: {r.free_remaining if r.free_remaining is not None else '—'}\n"
            f"Баланс: {f'{r.balance_ton:f} TON' if r.balance_ton is not None else '—'}{stopped}")


def owner_values(f) -> str:
    values = [f"@{u}" for u in (f.owner_usernames or [])] + [str(i) for i in (f.owner_telegram_ids or [])]
    return escape(", ".join(values)) if values else "любой"


def filter_text(f) -> str:  # UserFilter
    def lst(v: list[str]) -> str:
        return escape(", ".join(v)) if v else "любые"
    price = "любая"
    if f.price_min is not None or f.price_max is not None:
        price = f"{f.price_min if f.price_min is not None else '…'} – {f.price_max if f.price_max is not None else '…'} {f.price_currency}"
    on = lambda b: "✅" if b else "▫️"  # noqa: E731
    return ("⚙️ <b>Настройки</b>\n\n"
            f"{on(f.notifications_enabled)} Уведомления\n{on(f.notify_new_listing)} Новые листинги\n"
            f"{on(f.notify_price_changed)} Изменение цены\n{on(f.notify_owner_found)} Найден владелец\n\n"
            f"Коллекции: {lst(f.collections)}\nМодели: {lst(f.models)}\nФоны: {lst(f.backdrops)}\n"
            f"Паттерны: {lst(f.patterns)}\nЦена: {escape(price)}\n\n"
            f"{on(f.require_username)} Только с username владельца\n{on(f.require_telegram_id)} Только с Telegram ID владельца\n"
            f"Владелец: {owner_values(f)}\n\n"
            "Изменить: /filter (см. /help)")
