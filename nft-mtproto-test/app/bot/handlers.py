"""User commands: /start /help /search /stats /settings /filter and menu callbacks."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import formatters as fmt
from app.bot import keyboards as kb
from app.database.models import UserFilter
from app.database.types import utcnow
from app.services import queries

PAGE_SIZE = 5
MAX_ITEMS = 20
ITEM_RE = re.compile(r"^[\w .'\-]{1,64}$")
USERNAME_RE = re.compile(r"^@?[A-Za-z][A-Za-z0-9_]{3,31}$")
LIST_FIELDS = {"collections", "models", "backdrops", "patterns"}
TOGGLE_FIELDS = {f for f, _ in kb.TOGGLES}


async def ensure_filter(s: AsyncSession, user_id: int) -> UserFilter:
    f = (await s.execute(select(UserFilter).where(UserFilter.user_id == user_id)
                         .order_by(UserFilter.id).limit(1))).scalar_one_or_none()
    if f is None:
        # Notifications start off: without filters a user would get every listing on the market.
        f = UserFilter(user_id=user_id, notifications_enabled=False, collections=[], models=[], backdrops=[],
                       patterns=[], owner_usernames=[], owner_telegram_ids=[])
        s.add(f)
        await s.flush()
    return f


class FilterInputError(ValueError):
    pass


def apply_filter_command(f: UserFilter, args: str) -> None:
    """'/filter collections IonGem, MoonPendant' etc. Raises FilterInputError with a user-facing message."""
    key, _, value = (args or "").strip().partition(" ")
    key, value = key.lower(), value.strip()
    if key in LIST_FIELDS:
        items = [i.strip() for i in value.split(",") if i.strip()]
        if len(items) > MAX_ITEMS or any(not ITEM_RE.match(i) for i in items):
            raise FilterInputError(f"До {MAX_ITEMS} значений через запятую, только буквы, цифры, пробел, дефис.")
        setattr(f, key, items)
    elif key == "price":
        parts = value.split()
        if parts == ["off"] or not parts:
            f.price_min = f.price_max = None
            return
        currency = "XTR"
        if parts[-1].upper() in ("XTR", "TON", "STARS"):
            currency = "TON" if parts.pop().upper() == "TON" else "XTR"
        if len(parts) != 2:
            raise FilterInputError("Формат: /filter price 100 5000 XTR  (минимум или максимум можно заменить на -)")
        try:
            low, high = (None if p == "-" else Decimal(p) for p in parts)
        except InvalidOperation:
            raise FilterInputError("Цена должна быть числом.") from None
        if any(v is not None and (v < 0 or not v.is_finite()) for v in (low, high)) or (
                low is not None and high is not None and low > high):
            raise FilterInputError("Некорректный диапазон цены.")
        f.price_min, f.price_max, f.price_currency = low, high, currency
    elif key == "owner":
        # Owner values are compared with the owner of each scanned NFT; they never start a search by user.
        usernames, ids = [], []
        for item in (i.strip() for i in value.split(",") if i.strip()):
            if item.isdigit() and len(item) <= 20:
                ids.append(int(item))
            elif USERNAME_RE.match(item):
                usernames.append(item.lstrip("@"))
            else:
                raise FilterInputError("Формат: /filter owner @username, 8556771897  (username 4–32 символа: буквы, цифры, _)")
        if len(usernames) + len(ids) > MAX_ITEMS:
            raise FilterInputError(f"Не более {MAX_ITEMS} владельцев.")
        f.owner_usernames, f.owner_telegram_ids = usernames, ids
    elif key == "reset":
        f.collections, f.models, f.backdrops, f.patterns = [], [], [], []
        f.price_min = f.price_max = None
        f.require_username = f.require_telegram_id = False
        f.owner_usernames, f.owner_telegram_ids = [], []
    else:
        raise FilterInputError("Неизвестный параметр. Доступно: collections, models, backdrops, patterns, price, "
                               "owner, reset.")


async def _settings_view(deps, user_id: int) -> str:
    async with deps.sessions.begin() as s:
        return fmt.filter_text(await ensure_filter(s, user_id))


async def _after_filter_change(deps, filter_id: int, enabled: bool) -> str:
    if not enabled or deps.pipeline is None:
        return ""
    queued = await deps.pipeline.backfill(filter_id)
    return f"\nПроверяю сохранённые листинги: {queued} в очереди." if queued else ""


async def start(message: Message) -> None:
    await message.answer(fmt.START, reply_markup=kb.main_menu())


async def help_cmd(message: Message) -> None:
    await message.answer(fmt.HELP, disable_web_page_preview=True)


async def _send_search(message: Message, deps, text: str) -> None:
    async with deps.sessions() as s:
        cards = await queries.search(s, text)
    if not cards:
        await message.answer("NFT не найден в локальной базе. Примеры: <code>MoonPendant-71710</code>, "
                             "<code>Moon Pendant 71710</code>, <code>IonGem</code>.")
    elif len(cards) == 1:
        await message.answer(fmt.card_text(cards[0]), reply_markup=kb.card_kb(cards[0].gift.slug),
                             disable_web_page_preview=True)
    else:
        await message.answer("\n".join(fmt.short_line(c) for c in cards) + "\n\nУточните номер для полной карточки.")


async def search_cmd(message: Message, command: CommandObject, deps) -> None:
    if not command.args:
        await message.answer("Напишите: /search MoonPendant-71710")
        return
    await _send_search(message, deps, command.args)


async def stats_cmd(message: Message, deps) -> None:
    async with deps.sessions() as s:
        st = await queries.stats(s, utcnow())
    await message.answer(fmt.stats_text(st))


async def settings_cmd(message: Message, deps, db_user_id: int) -> None:
    await message.answer(await _settings_view(deps, db_user_id), reply_markup=kb.settings_kb())


async def filter_cmd(message: Message, command: CommandObject, deps, db_user_id: int) -> None:
    async with deps.sessions.begin() as s:
        f = await ensure_filter(s, db_user_id)
        try:
            apply_filter_command(f, command.args or "")
        except FilterInputError as e:
            await message.answer(str(e))
            return
        await s.flush()
        fid, enabled, text = f.id, f.notifications_enabled, fmt.filter_text(f)
    extra = await _after_filter_change(deps, fid, enabled)
    await message.answer("Фильтр сохранён." + extra + "\n\n" + text, reply_markup=kb.settings_kb())


async def menu_cb(cb: CallbackQuery) -> None:
    await cb.message.edit_text(fmt.START, reply_markup=kb.main_menu())
    await cb.answer()


async def help_cb(cb: CallbackQuery) -> None:
    await cb.message.answer(fmt.HELP, disable_web_page_preview=True)
    await cb.answer()


async def search_cb(cb: CallbackQuery) -> None:
    await cb.message.answer("Напишите: /search MoonPendant-71710")
    await cb.answer()


async def stats_cb(cb: CallbackQuery, deps) -> None:
    async with deps.sessions() as s:
        st = await queries.stats(s, utcnow())
    await cb.message.answer(fmt.stats_text(st))
    await cb.answer()


async def listings_cb(cb: CallbackQuery, deps) -> None:
    page = int(cb.data.split(":")[1])
    async with deps.sessions() as s:
        cards, total = await queries.recent_listings(s, page, PAGE_SIZE)
    text = "\n\n".join(fmt.card_text(c) for c in cards) if cards else "Листингов пока нет."
    await cb.message.edit_text(text, reply_markup=kb.pager(page, total, PAGE_SIZE), disable_web_page_preview=True)
    await cb.answer()


async def matches_cb(cb: CallbackQuery, deps, db_user_id: int) -> None:
    async with deps.sessions.begin() as s:
        f = await ensure_filter(s, db_user_id)
        cards = await queries.matches_for_user(s, f, utcnow())
    text = "\n".join(fmt.short_line(c) for c in cards) if cards else "Под ваш фильтр пока ничего не найдено."
    await cb.message.answer(text)
    await cb.answer()


async def settings_cb(cb: CallbackQuery, deps, db_user_id: int) -> None:
    await cb.message.answer(await _settings_view(deps, db_user_id), reply_markup=kb.settings_kb())
    await cb.answer()


async def toggle_cb(cb: CallbackQuery, deps, db_user_id: int) -> None:
    field = cb.data.split(":", 1)[1]
    if field not in TOGGLE_FIELDS:  # never setattr from user input without a whitelist
        await cb.answer()
        return
    async with deps.sessions.begin() as s:
        f = await ensure_filter(s, db_user_id)
        setattr(f, field, not getattr(f, field))
        await s.flush()
        fid, enabled, text = f.id, f.notifications_enabled, fmt.filter_text(f)
    extra = await _after_filter_change(deps, fid, enabled) if field == "notifications_enabled" else ""
    await cb.message.edit_text(text + extra, reply_markup=kb.settings_kb())
    await cb.answer("Сохранено")


async def noop_cb(cb: CallbackQuery) -> None:
    await cb.answer()


async def plain_text(message: Message, deps) -> None:
    """Plain text is treated as a search query."""
    await _send_search(message, deps, message.text)


def build_router() -> Router:
    """A fresh router per dispatcher: aiogram attaches a router to one parent only (bot restarts)."""
    r = Router(name="user")
    r.message.register(start, CommandStart())
    r.message.register(help_cmd, Command("help"))
    r.message.register(search_cmd, Command("search"))
    r.message.register(stats_cmd, Command("stats"))
    r.message.register(settings_cmd, Command("settings"))
    r.message.register(filter_cmd, Command("filter"))
    r.callback_query.register(menu_cb, F.data == "menu")
    r.callback_query.register(help_cb, F.data == "help")
    r.callback_query.register(search_cb, F.data == "search")
    r.callback_query.register(stats_cb, F.data == "stats")
    r.callback_query.register(listings_cb, F.data.regexp(r"^list:\d{1,5}$"))
    r.callback_query.register(matches_cb, F.data == "matches")
    r.callback_query.register(settings_cb, F.data == "settings")
    r.callback_query.register(toggle_cb, F.data.startswith("toggle:"))
    r.callback_query.register(noop_cb, F.data == "noop")
    r.message.register(plain_text, F.text & ~F.text.startswith("/"))
    return r
