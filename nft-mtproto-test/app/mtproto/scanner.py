"""Listing scanner (T3.2): polls the first pages of payments.getResaleStarGifts per gift type.

Discovery only: no network calls other than MTProto, no notifications. Each page is stored in its own
transaction together with the downstream hook (stage 1 filter + queue), so a crash loses nothing that
was already acknowledged.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telethon.errors import RPCError
from telethon.tl.functions.payments import GetResaleStarGiftsRequest, GetStarGiftsRequest

from app.database.models import ScanState
from app.database.types import utcnow
from app.mtproto.client import FloodWaitTooLong, MTProtoError
from app.mtproto.parser import parse_page, to_raw
from app.services import listings
from app.services.listings import ListingEvent
from app.utils.log import MTPROTO

log = logging.getLogger(MTPROTO)

PAGE_SIZE = 100  # Telegram's maximum; 101 is silently cut to 100
TYPES_REFRESH = timedelta(hours=1)

EventHook = Callable[[AsyncSession, list[ListingEvent]], Awaitable[None]]


@dataclass
class CycleStats:
    types: int = 0
    types_failed: int = 0
    pages: int = 0
    received: int = 0
    new_gifts: int = 0
    new_listings: int = 0
    duplicates: int = 0
    price_changes: int = 0
    inactive: int = 0
    stopped: str | None = None


@dataclass
class ScannerStatus:
    running: bool = False
    cycles: int = 0
    last_cycle_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_stats: CycleStats = field(default_factory=CycleStats)


class ListingScanner:
    def __init__(self, client: Callable[[Any], Awaitable[Any]], sessions: async_sessionmaker[AsyncSession],
                 interval_s: int, on_events: EventHook | None = None, pages_per_type: int = 1,
                 clock: Callable[[], datetime] = utcnow) -> None:
        self._client = client
        self._sessions = sessions
        self._interval = interval_s
        self._on_events = on_events
        self._pages = pages_per_type
        self._clock = clock
        self._types: list[tuple[int, str | None]] = []
        self._types_at: datetime | None = None
        self.status = ScannerStatus()

    async def gift_types(self) -> list[tuple[int, str | None]]:
        now = self._clock()
        if not self._types or self._types_at is None or now - self._types_at > TYPES_REFRESH:
            raw = to_raw(await self._client(GetStarGiftsRequest(hash=0)))
            self._types = [(g["id"], g.get("title")) for g in raw.get("gifts") or []
                           if g.get("_") == "StarGift" and g.get("availability_resale")]
            self._types_at = now
            log.info("gift types with resale listings: %s", len(self._types))
        return self._types

    async def _store(self, parsed: list, seen_at: datetime, stats: CycleStats) -> None:
        async with self._sessions.begin() as s:
            events, st = await listings.ingest(s, parsed, seen_at)
            if self._on_events and events:
                await self._on_events(s, events)
        stats.received += st.received
        stats.new_gifts += st.new_gifts
        stats.new_listings += st.new_listings
        stats.duplicates += st.duplicates
        stats.price_changes += st.price_changes

    async def _close_missing(self, gift_type_id: int, seen: set[str], at: datetime, stats: CycleStats) -> None:
        async with self._sessions.begin() as s:
            events = await listings.close_missing(s, gift_type_id, seen, at)
            if self._on_events and events:
                await self._on_events(s, events)
        stats.inactive += len(events)

    async def _save_state(self, gift_type_id: int, title: str | None, at: datetime, *, ok: bool,
                          full: bool = False, count: int | None = None, error: str | None = None) -> None:
        async with self._sessions.begin() as s:
            state = (await s.execute(select(ScanState).where(ScanState.gift_type_id == gift_type_id))).scalar_one_or_none()
            if state is None:
                state = ScanState(gift_type_id=gift_type_id)
                s.add(state)
            state.title, state.last_scan_at, state.last_error = title, at, error
            if ok:
                state.last_success_at, state.last_count = at, count
            if full:
                state.last_full_scan_at = at

    async def scan_type(self, gift_type_id: int, title: str | None, stats: CycleStats) -> None:
        at = self._clock()
        offset, seen, offsets, exhausted, count = "", set(), set(), False, None
        for _ in range(self._pages):
            res = await self._client(GetResaleStarGiftsRequest(gift_id=gift_type_id, offset=offset, limit=PAGE_SIZE))
            page = parse_page(res, at)
            stats.pages += 1
            count = page.count
            for err in page.errors:
                log.warning("type %s: skipped item: %s", gift_type_id, err)
            await self._store(page.listings, at, stats)
            seen.update(p.slug for p in page.listings)
            if page.next_offset is None:
                exhausted = True
                break
            # An empty page with a next_offset, or a repeated offset, would loop forever (see final_report.md).
            if not page.listings or page.next_offset in offsets:
                log.warning("type %s: offset loop guard stopped pagination", gift_type_id)
                break
            offsets.add(page.next_offset)
            offset = page.next_offset
        if exhausted:
            await self._close_missing(gift_type_id, seen, at, stats)
        await self._save_state(gift_type_id, title, at, ok=True, full=exhausted, count=count)

    async def run_cycle(self) -> CycleStats:
        stats = CycleStats()
        started = self._clock()
        self.status.last_cycle_at = started
        log.info("scan cycle started")
        try:
            types = await self.gift_types()
        except (RPCError, MTProtoError) as e:
            stats.stopped = f"gift types: {type(e).__name__}"
            self.status.last_error = stats.stopped
            log.error("scan cycle aborted: %s", stats.stopped)
            return stats
        for gift_type_id, title in types:
            stats.types += 1
            try:
                await self.scan_type(gift_type_id, title, stats)
            except (FloodWaitTooLong, MTProtoError) as e:
                stats.stopped = type(e).__name__
                await self._save_state(gift_type_id, title, self._clock(), ok=False, error=str(e))
                log.error("scan cycle stopped on type %s: %s", gift_type_id, e)
                break
            except Exception as e:  # one bad type must not stop the scanner
                stats.types_failed += 1
                await self._save_state(gift_type_id, title, self._clock(), ok=False, error=f"{type(e).__name__}: {e}")
                log.exception("type %s failed", gift_type_id)
        async with self._sessions.begin() as s:
            stale = await listings.mark_stale(s, self._clock())
            if self._on_events and stale:
                await self._on_events(s, stale)
        self.status.cycles += 1
        self.status.last_stats = stats
        if stats.stopped is None:
            self.status.last_success_at = started
            self.status.last_error = None
        else:
            self.status.last_error = stats.stopped
        log.info("scan cycle done: types=%s pages=%s received=%s new_gifts=%s new_listings=%s duplicates=%s "
                 "price_changes=%s inactive=%s failed=%s", stats.types, stats.pages, stats.received, stats.new_gifts,
                 stats.new_listings, stats.duplicates, stats.price_changes, stats.inactive, stats.types_failed)
        return stats

    async def run(self, stop: asyncio.Event) -> None:
        self.status.running = True
        try:
            while not stop.is_set():
                await self.run_cycle()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.status.running = False
