from __future__ import annotations

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup

from app.bot.formatters import nft_url


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(text="Новые листинги", callback_data="list:0"), B(text="Мои совпадения", callback_data="matches")],
        [B(text="Поиск NFT", callback_data="search"), B(text="Настройки", callback_data="settings")],
        [B(text="Статистика", callback_data="stats"), B(text="Помощь", callback_data="help")],
    ])


def pager(page: int, total: int, size: int) -> InlineKeyboardMarkup:
    row = []
    if page > 0:
        row.append(B(text="◀️", callback_data=f"list:{page - 1}"))
    row.append(B(text=f"{page + 1}/{max((total + size - 1) // size, 1)}", callback_data="noop"))
    if (page + 1) * size < total:
        row.append(B(text="▶️", callback_data=f"list:{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[row, [B(text="Меню", callback_data="menu")]])


def card_kb(slug: str) -> InlineKeyboardMarkup | None:
    url = nft_url(slug)
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="Открыть NFT", url=url)]]) if url else None


TOGGLES = (("notifications_enabled", "Уведомления"), ("notify_new_listing", "Новые листинги"),
           ("notify_price_changed", "Изменение цены"), ("notify_owner_found", "Найден владелец"),
           ("require_username", "Только с username"), ("require_telegram_id", "Только с Telegram ID"))


def settings_kb() -> InlineKeyboardMarkup:
    rows = [[B(text=label, callback_data=f"toggle:{field}")] for field, label in TOGGLES]
    return InlineKeyboardMarkup(inline_keyboard=rows + [[B(text="Меню", callback_data="menu")]])
