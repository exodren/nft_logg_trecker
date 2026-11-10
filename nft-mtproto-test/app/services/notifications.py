"""Notification Service (T6): sends PENDING NFT notification rows to subscribers, records delivery status.

Rows are created by the pipeline (one per user per event, unique). Here: Telegram rate limits
(RetryAfter), blocked users, bounded parallelism. Three independent streams exist:
NFT notifications (here, to subscribers), service logs (log_sender.py, LOG_GROUP_ID only), local file logs.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import formatters as fmt
from app.bot import keyboards as kb
from app.database.models import Gift, Listing, Notification, User
from app.database.types import utcnow
from app.providers.base import HIDDEN_OWNER_TEXT, OwnerStatus
from app.services import queries
from app.services.listings import NEW_LISTING, PRICE_CHANGED
from app.utils.log import BOT

log = logging.getLogger(BOT)

Send = Callable[..., Awaitable[Any]]  # send(chat_id, text, reply_markup=...)
MAX_PARALLEL = 5
MAX_ATTEMPTS = 3
BATCH = 30
IDLE_POLL_S = 3.0
HEADERS = {NEW_LISTING: "🆕 <b>Новый листинг</b>", PRICE_CHANGED: "💸 <b>Цена изменилась</b>"}


class NotificationSender:
    """NFT notifications to subscribers only. Service/admin events are not sent from here: they are logged
    and reach LOG_GROUP_ID through app/services/log_sender.py."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], send: Send,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._sessions = sessions
        self._send = send
        self._sleep = sleep
        self._sem = asyncio.Semaphore(MAX_PARALLEL)

    async def _deliver(self, note_id: int) -> str:
        async with self._sessions() as s:
            note = await s.get(Notification, note_id)
            user = await s.get(User, note.user_id)
            listing = await s.get(Listing, note.listing_id) if note.listing_id else None
            gift = await s.get(Gift, listing.gift_id) if listing else None
            if not user.is_active or gift is None:
                result = "SKIPPED"
            else:
                c = await queries.card(s, gift, listing)
                text = f"{HEADERS.get(note.event_type, '')}\n\n{fmt.card_text(c)}"
                result = ""
                if c.owner is not None and c.owner.status is OwnerStatus.HIDDEN:
                    log.info("notification %s for %s sent without owner: %s", note_id, gift.slug, HIDDEN_OWNER_TEXT)
        if result == "SKIPPED":
            return await self._finish(note_id, "SKIPPED", "user inactive or listing gone")
        async with self._sem:
            try:
                await self._send(user.telegram_id, text, reply_markup=kb.card_kb(gift.slug))
            except TelegramRetryAfter as e:
                log.warning("Telegram RetryAfter %ss", e.retry_after)
                await self._sleep(e.retry_after)
                return "PENDING"  # retried on the next pass
            except TelegramForbiddenError:
                async with self._sessions.begin() as s:
                    (await s.get(User, note.user_id)).is_active = False
                return await self._finish(note_id, "FAILED", "bot blocked by user")
            except TelegramBadRequest as e:
                return await self._finish(note_id, "FAILED", f"bad request: {e.message}")
            except Exception as e:
                return await self._attempt_failed(note_id, f"{type(e).__name__}: {e}")
        return await self._finish(note_id, "SENT", None)

    async def _finish(self, note_id: int, status: str, error: str | None) -> str:
        async with self._sessions.begin() as s:
            note = await s.get(Notification, note_id)
            note.status, note.error_message = status, error
            note.attempts += 1
            if status == "SENT":
                note.sent_at = utcnow()
        if status == "FAILED":
            log.warning("notification %s failed: %s", note_id, error)
        return status

    async def _attempt_failed(self, note_id: int, error: str) -> str:
        async with self._sessions.begin() as s:
            note = await s.get(Notification, note_id)
            note.attempts += 1
            note.error_message = error[:1000]
            if note.attempts >= MAX_ATTEMPTS:
                note.status = "FAILED"
        log.warning("notification %s attempt failed: %s", note_id, error)
        return "FAILED" if note.attempts >= MAX_ATTEMPTS else "PENDING"

    async def send_pending(self, batch: int = BATCH) -> int:
        async with self._sessions() as s:
            ids = (await s.execute(select(Notification.id).where(Notification.status == "PENDING")
                                   .order_by(Notification.id).limit(batch))).scalars().all()
        results = await asyncio.gather(*(self._deliver(i) for i in ids))
        return sum(r == "SENT" for r in results)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                sent = await self.send_pending()
            except Exception:
                log.exception("notification pass failed")
                sent = 0
            if not sent:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=IDLE_POLL_S)
                except asyncio.TimeoutError:
                    pass
