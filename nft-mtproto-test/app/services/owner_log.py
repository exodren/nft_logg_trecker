"""Owner log: cards of found owners (listings that matched a user filter) go to OWNER_LOG_GROUP_ID.

The group card has NFT data only and a "Занять лог" button. The first member who presses it gets the full
card (with the owner) in a private chat; the group message becomes "🔒 Лог занят". Hidden owners never get
here (Pipeline queues only FOUND with a username). Service logs stay in LOG_GROUP_ID (log_sender.py).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import formatters as fmt
from app.bot import keyboards as kb
from app.database.models import Gift, Listing, OwnerLogPost
from app.database.types import utcnow
from app.services import queries
from app.utils.log import BOT

log = logging.getLogger(BOT)

MAX_ATTEMPTS = 3
IDLE_POLL_S = 3.0
GROUP_GAP_S = 3.1  # Telegram allows ~20 messages/min per group
HEADER = "🆕 <b>Найден владелец</b>"
CLAIMED_TEXT = "🔒 Лог занят"


async def _card(s: AsyncSession, listing_id: int) -> queries.Card | None:
    listing = await s.get(Listing, listing_id)
    gift = await s.get(Gift, listing.gift_id) if listing else None
    return await queries.card(s, gift, listing) if gift else None


def claim_kb(post_id: int, slug: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="Занять лог", callback_data=f"claim:{post_id}")]]
    if fmt.nft_url(slug):
        rows.append([InlineKeyboardButton(text="Открыть NFT", url=fmt.nft_url(slug))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class OwnerLogSender:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], bot: Bot, chat_id: int) -> None:
        self._sessions, self._bot, self.chat_id = sessions, bot, chat_id

    async def _set(self, post_id: int, **values: object) -> None:
        async with self._sessions.begin() as s:
            await s.execute(update(OwnerLogPost).where(OwnerLogPost.id == post_id).values(**values))

    async def post_one(self) -> bool:
        async with self._sessions() as s:
            post = (await s.execute(select(OwnerLogPost).where(OwnerLogPost.status == "PENDING")
                                    .order_by(OwnerLogPost.id).limit(1))).scalar_one_or_none()
            if post is None:
                return False
            post_id, attempts = post.id, post.attempts
            c = await _card(s, post.listing_id)
        if c is None or c.listing is None or not c.listing.is_active:
            await self._set(post_id, status="SKIPPED", error_message="listing gone")
            return True
        text = f"{HEADER}\n\n{fmt.card_text(replace(c, owner=None))}"  # owner only after claim
        try:
            msg = await self._bot.send_message(self.chat_id, text, reply_markup=claim_kb(post_id, c.gift.slug),
                                               disable_web_page_preview=True)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            return True
        except Exception as e:
            failed = isinstance(e, TelegramBadRequest) or attempts + 1 >= MAX_ATTEMPTS
            await self._set(post_id, attempts=attempts + 1, error_message=f"{type(e).__name__}: {e}"[:1000],
                            status="FAILED" if failed else "PENDING")
            log.warning("owner log post %s failed: %s", post_id, type(e).__name__)
            return True
        await self._set(post_id, status="SENT", message_id=msg.message_id, attempts=attempts + 1)
        await asyncio.sleep(GROUP_GAP_S)
        return True

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                busy = await self.post_one()
            except Exception:
                log.exception("owner log pass failed")
                busy = False
            if not busy:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=IDLE_POLL_S)
                except asyncio.TimeoutError:
                    pass


async def claim(sessions: async_sessionmaker[AsyncSession], bot: Bot, chat_id: int, post_id: int,
                user_tg_id: int) -> str:
    """First press wins: SENT -> CLAIMED is one conditional UPDATE. Returns the text for the button popup."""
    async with sessions.begin() as s:
        won = (await s.execute(update(OwnerLogPost).where(OwnerLogPost.id == post_id, OwnerLogPost.status == "SENT")
                               .values(status="CLAIMED", claimed_by=user_tg_id, claimed_at=utcnow()))).rowcount
    if not won:
        return "Лог уже занят"
    async with sessions() as s:
        post = await s.get(OwnerLogPost, post_id)
        c = await _card(s, post.listing_id)
    try:
        await bot.send_message(user_tg_id, f"{HEADER}\n\n{fmt.card_text(c)}", reply_markup=kb.card_kb(c.gift.slug))
    except (TelegramForbiddenError, TelegramBadRequest):
        async with sessions.begin() as s:  # cannot write to this user: give the log back to the group
            await s.execute(update(OwnerLogPost).where(OwnerLogPost.id == post_id)
                            .values(status="SENT", claimed_by=None, claimed_at=None))
        return "Сначала напишите боту /start в личку, потом нажмите ещё раз"
    try:
        await bot.edit_message_text(CLAIMED_TEXT, chat_id=chat_id, message_id=post.message_id, reply_markup=None)
    except TelegramBadRequest as e:
        log.warning("owner log %s: could not mark as claimed: %s", post_id, e.message)
    log.info("owner log %s (%s) claimed by %s", post_id, c.gift.slug, user_tg_id)
    return "Лог отправлен вам в личку"


def build_router() -> Router:
    router = Router()

    @router.callback_query(F.data.startswith("claim:"))
    async def on_claim(cb: CallbackQuery, deps) -> None:  # noqa: ANN001
        chat_id = deps.settings.owner_log_group_id
        post_id = cb.data.split(":", 1)[1]
        if chat_id is None or cb.message is None or cb.message.chat.id != chat_id or not post_id.isdigit():
            await cb.answer()
            return
        await cb.answer(await claim(deps.sessions, cb.bot, chat_id, int(post_id), cb.from_user.id), show_alert=True)

    return router
