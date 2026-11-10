"""Read-side queries for the bot: listings, search, card data, statistics. Local DB only, never the API."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (Gift, Listing, Notification, Owner, OwnerLookupCache, ProcessingTask, User,
                                 UserFilter)
from app.providers.base import OwnerData
from app.services import cache
from app.services.filters import has_owner_criteria, norm, stage1, stage2
from app.services.pipeline import view

MAX_QUERY_LEN = 64
SLUG_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*)-(\d{1,9})$")
TITLE_NUM_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 ]*?)\s*#?\s*(\d{1,9})$")


@dataclass(frozen=True)
class Card:
    gift: Gift
    listing: Listing | None
    owner: OwnerData | None  # cached lookup, any status
    lookup_status: str | None  # listing.owner_lookup_status


async def card(s: AsyncSession, gift: Gift, listing: Listing | None) -> Card:
    row = (await s.execute(select(OwnerLookupCache).where(OwnerLookupCache.gift_id == gift.id))).scalar_one_or_none()
    owner = None
    if row is not None:
        owner = cache.to_owner_data(row, await s.get(Owner, row.owner_id) if row.owner_id else None)
    return Card(gift, listing, owner, listing.owner_lookup_status if listing else None)


async def latest_listing(s: AsyncSession, gift_id: int) -> Listing | None:
    return (await s.execute(select(Listing).where(Listing.gift_id == gift_id)
                            .order_by(Listing.is_active.desc(), Listing.first_seen_at.desc()).limit(1))).scalar_one_or_none()


async def recent_listings(s: AsyncSession, page: int, size: int) -> tuple[list[Card], int]:
    total = (await s.execute(select(func.count()).select_from(Listing).where(Listing.is_active.is_(True)))).scalar()
    rows = (await s.execute(select(Listing, Gift).join(Gift).where(Listing.is_active.is_(True))
                            .order_by(Listing.first_seen_at.desc(), Listing.id.desc())
                            .offset(page * size).limit(size))).all()
    return [await card(s, g, l) for l, g in rows], total


def parse_query(text: str) -> tuple[str, str | None, int | None]:
    """-> (kind, collection, number). kind: slug | collection | number | invalid."""
    q = " ".join((text or "").split())[:MAX_QUERY_LEN]
    if m := SLUG_RE.match(q):
        return "slug", m.group(1), int(m.group(2))
    if q.isdigit() and len(q) <= 9:
        return "number", None, int(q)
    if m := TITLE_NUM_RE.match(q):
        return "slug", m.group(1), int(m.group(2))
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9 ]{1,40}", q):
        return "collection", q, None
    return "invalid", None, None


async def search(s: AsyncSession, text: str, limit: int = 10) -> list[Card]:
    kind, collection, number = parse_query(text)
    if kind == "invalid":
        return []
    stmt = select(Gift)
    if collection is not None:
        key = norm(collection)
        stmt = stmt.where(or_(func.lower(Gift.collection) == key,
                              func.lower(func.replace(Gift.title, " ", "")) == key))
    if number is not None:
        stmt = stmt.where(Gift.number == number)
    gifts = (await s.execute(stmt.order_by(Gift.updated_at.desc()).limit(limit))).scalars().all()
    return [await card(s, g, await latest_listing(s, g.id)) for g in gifts]


async def matches_for_user(s: AsyncSession, f: UserFilter, now: datetime, limit: int = 10) -> list[Card]:
    """Active listings matching the user's filter: stage 1 on listing data, stage 2 on cached owner data."""
    rows = (await s.execute(select(Listing, Gift).join(Gift).where(Listing.is_active.is_(True))
                            .order_by(Listing.first_seen_at.desc()).limit(500))).all()
    out = []
    for listing, gift in rows:
        if not stage1(f, view(listing, gift)):
            continue
        c = await card(s, gift, listing)
        if has_owner_criteria(f) and not stage2(f, c.owner):
            continue
        out.append(c)
        if len(out) >= limit:
            break
    return out


@dataclass(frozen=True)
class Stats:
    gifts: int
    active_listings: int
    new_listings_24h: int
    processed_gifts: int
    owners_found: int  # with a username
    owners_no_username: int
    owners_hidden: int
    processing_errors: int


async def stats(s: AsyncSession, now: datetime) -> Stats:
    async def n(stmt) -> int:
        return (await s.execute(stmt)).scalar() or 0
    by_status = dict((await s.execute(select(OwnerLookupCache.status, func.count()).group_by(OwnerLookupCache.status))).all())
    return Stats(
        gifts=await n(select(func.count()).select_from(Gift)),
        active_listings=await n(select(func.count()).select_from(Listing).where(Listing.is_active.is_(True))),
        new_listings_24h=await n(select(func.count()).select_from(Listing).where(
            Listing.first_seen_at >= now - timedelta(hours=24))),
        processed_gifts=sum(by_status.values()),
        owners_found=by_status.get("FOUND", 0),
        owners_no_username=by_status.get("NO_USERNAME", 0),
        owners_hidden=by_status.get("HIDDEN", 0),
        processing_errors=by_status.get("ERROR", 0) + await n(select(func.count()).select_from(ProcessingTask).where(
            ProcessingTask.status == "failed")),
    )


async def admin_counts(s: AsyncSession) -> dict[str, int]:
    async def n(stmt) -> int:
        return (await s.execute(stmt)).scalar() or 0
    return {
        "users": await n(select(func.count()).select_from(User)),
        "active_users": await n(select(func.count()).select_from(User).where(User.is_active.is_(True))),
        "notifications_pending": await n(select(func.count()).select_from(Notification).where(Notification.status == "PENDING")),
        "notifications_failed": await n(select(func.count()).select_from(Notification).where(Notification.status == "FAILED")),
    }


async def recent_errors(s: AsyncSession, limit: int = 5) -> list[str]:
    rows = (await s.execute(select(ProcessingTask.dedup_key, ProcessingTask.last_error).where(
        ProcessingTask.status.in_(("failed", "retry")), ProcessingTask.last_error.is_not(None))
        .order_by(ProcessingTask.updated_at.desc()).limit(limit))).all()
    return [f"{k}: {e}" for k, e in rows]
