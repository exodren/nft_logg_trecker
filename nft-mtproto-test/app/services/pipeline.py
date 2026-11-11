"""Listing pipeline (§20): stage 1 -> owner lookup -> stage 2 -> notification rows. F.1-F.4.

on_events runs inside the scanner's page transaction, so a stored listing and its queued lookup commit
together. Every listing is stored whatever the filters say; a listing nobody wants gets NOT_NEEDED and
costs no request.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import Gift, Listing, Notification, Owner, OwnerLogPost, User, UserFilter
from app.database.types import utcnow
from app.providers.base import OwnerData, OwnerStatus
from app.services import cache
from app.services.enrichment import OwnerResolver
from app.services.filters import ListingView, has_owner_criteria, stage1, stage2
from app.services.listings import NEW_LISTING, PRICE_CHANGED, ListingEvent
from app.services.queue import HIGH, LOW, Task, TaskQueue, enqueue
from app.utils.log import DATABASE

log = logging.getLogger(DATABASE)

OWNER_LOOKUP = "owner_lookup"
UNAVAILABLE_RETRY = timedelta(minutes=15)
# ponytail: backfill looks at the newest N active listings; page through older ones if users need it.
BACKFILL_LIMIT = 500
IDLE_POLL_S = 2.0


def view(listing: Listing, gift: Gift) -> ListingView:
    return ListingView(gift.collection, gift.title, gift.model, gift.backdrop, gift.pattern,
                       listing.price_stars, listing.price_ton)


async def active_filters(s: AsyncSession, flag: str) -> list[UserFilter]:
    return list((await s.execute(select(UserFilter).join(User).where(
        UserFilter.is_active.is_(True), UserFilter.notifications_enabled.is_(True),
        getattr(UserFilter, flag).is_(True), User.is_active.is_(True)))).scalars())


async def add_notification(s: AsyncSession, user_id: int, listing_id: int, event_type: str, event_key: str) -> bool:
    """One event = one notification per user (unique user_id + event_key)."""
    exists = (await s.execute(select(Notification.id).where(
        Notification.user_id == user_id, Notification.event_key == event_key))).first()
    if exists:
        return False
    s.add(Notification(user_id=user_id, listing_id=listing_id, event_type=event_type, event_key=event_key,
                       status="PENDING"))
    await s.flush()
    return True


class Pipeline:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], queue: TaskQueue, resolver: OwnerResolver,
                 clock: Callable[[], datetime] = utcnow, owner_log: bool = False) -> None:
        self._sessions = sessions
        self._owner_log = owner_log  # also post found owners of matched listings to OWNER_LOG_GROUP_ID
        self.queue = queue
        self._resolver = resolver
        self._clock = clock

    # --- F.1: stage 1 inside the scanner transaction ------------------------------------------------
    async def on_events(self, s: AsyncSession, events: list[ListingEvent]) -> None:
        new = [e for e in events if e.type == NEW_LISTING]
        if new:
            filters = await active_filters(s, "notify_new_listing")
            for e in new:
                listing, gift = await s.get(Listing, e.listing_id), await s.get(Gift, e.gift_id)
                if not any(stage1(f, view(listing, gift)) for f in filters):
                    listing.owner_lookup_status = "NOT_NEEDED"
                    continue
                listing.owner_lookup_status = "PENDING"
                await enqueue(s, OWNER_LOOKUP, f"owner:{gift.id}", priority=HIGH, listing_id=listing.id,
                              gift_id=gift.id, payload={"notify": True}, now=self._clock())
        changed = [e for e in events if e.type == PRICE_CHANGED]
        if changed:
            await self._price_changes(s, changed)

    async def _price_changes(self, s: AsyncSession, events: list[ListingEvent]) -> None:
        """Optional event (off by default): uses cached owner data only, never a paid request."""
        filters = await active_filters(s, "notify_price_changed")
        if not filters:
            return
        now = self._clock()
        for e in events:
            listing, gift = await s.get(Listing, e.listing_id), await s.get(Gift, e.gift_id)
            row = await cache.get_fresh(s, gift.id, now)
            owner = cache.to_owner_data(row, await s.get(Owner, row.owner_id) if row.owner_id else None) if row else None
            for f in filters:
                if stage1(f, view(listing, gift)) and stage2(f, owner):
                    await add_notification(s, f.user_id, listing.id, PRICE_CHANGED,
                                           f"{PRICE_CHANGED}:{listing.id}:{e.new_price}")

    # --- owner lookup task: K4.3 + F.2 + F.4 ------------------------------------------------------
    async def handle(self, task: Task) -> tuple[str, datetime | None]:
        """Returns ('done', None) or ('defer', until)."""
        async with self._sessions() as s:
            listing = await s.get(Listing, task.listing_id) if task.listing_id else None
        if listing is None or not listing.is_active:
            return "done", None  # sold, delisted or gone: no lookup for it any more
        res = await self._resolver.resolve(task.gift_id)
        async with self._sessions.begin() as s:
            listing = await s.get(Listing, task.listing_id)
            gift = await s.get(Gift, task.gift_id)
            listing.owner_lookup_status = "DEFERRED" if res.deferred else "DONE"
            # in group mode backfill results go to the group too (no DMs there), otherwise only to the bot views
            if task.payload.get("notify") or (self._owner_log and task.payload.get("backfill")):
                await self._notify_new_listing(s, listing, gift, res.data, owner_known=not res.deferred)
        if res.deferred:
            return "defer", res.retry_at or self._clock() + UNAVAILABLE_RETRY
        return "done", None

    async def _notify_new_listing(self, s: AsyncSession, listing: Listing, gift: Gift, owner: OwnerData,
                                  owner_known: bool) -> None:
        """F.2. Without owner data, users with owner criteria wait (no false match); the rest get the card now."""
        matched = False
        for f in await active_filters(s, "notify_new_listing"):
            if not stage1(f, view(listing, gift)):
                continue
            if has_owner_criteria(f) and not owner_known:
                continue
            if stage2(f, owner):
                matched = True
                if not self._owner_log:  # with OWNER_LOG_GROUP_ID everything goes through the group, no DMs
                    await add_notification(s, f.user_id, listing.id, NEW_LISTING, f"{NEW_LISTING}:{listing.id}")
        public = owner.status is OwnerStatus.FOUND and bool((owner.username or "").strip())
        if matched and self._owner_log and public:  # sent by app/services/owner_log.py; never hidden owners
            if not (await s.execute(select(OwnerLogPost.id).where(OwnerLogPost.listing_id == listing.id))).first():
                s.add(OwnerLogPost(listing_id=listing.id))

    async def process_one(self) -> bool:
        task = await self.queue.claim()
        if task is None:
            return False
        try:
            if task.task_type != OWNER_LOOKUP:
                raise ValueError(f"unknown task type {task.task_type}")
            outcome, until = await self.handle(task)
        except Exception as e:  # one task must not stop the worker
            log.exception("task %s failed", task.id)
            await self.queue.fail(task.id, f"{type(e).__name__}: {e}")
            return True
        if outcome == "defer":
            await self.queue.defer(task.id, until, "owner data unavailable")
        else:
            await self.queue.complete(task.id)
        return True

    async def run_worker(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if not await self.process_one():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=IDLE_POLL_S)
                except asyncio.TimeoutError:
                    pass

    # --- F.3: backfill for a new or changed filter -----------------------------------------------
    async def backfill(self, filter_id: int) -> int:
        """Queue owner lookups (LOW priority) for stored active listings matching this filter's stage 1.
        Cache and budget apply as usual. Results are shown in the bot, not pushed as notifications."""
        queued = 0
        async with self._sessions.begin() as s:
            f = await s.get(UserFilter, filter_id)
            if f is None or not f.is_active:
                return 0
            rows = (await s.execute(select(Listing, Gift).join(Gift).where(Listing.is_active.is_(True))
                                    .order_by(Listing.first_seen_at.desc()).limit(BACKFILL_LIMIT))).all()
            now = self._clock()
            for listing, gift in rows:
                if not stage1(f, view(listing, gift)):
                    continue
                if await cache.get_fresh(s, gift.id, now) is not None:
                    continue  # already known, nothing to pay for
                await enqueue(s, OWNER_LOOKUP, f"owner:{gift.id}", priority=LOW, listing_id=listing.id,
                              gift_id=gift.id, payload={"backfill": True}, now=self._clock())
                if listing.owner_lookup_status == "NOT_NEEDED":
                    listing.owner_lookup_status = "PENDING"
                queued += 1
        log.info("backfill for filter %s: %s lookups queued", filter_id, queued)
        return queued
