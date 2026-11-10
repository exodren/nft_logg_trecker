"""NFT vs listing separation and event classification (T3.3, T3.4).

listing_identity = '{slug}:{episode}'. MTProto has no listing ID, so a new episode starts when a gift
reappears after LISTING_INACTIVE or when its seller_name (owner_name) changes. Limitation: delist and
relist by the same seller between two scans looks like one continuous listing.
A listing missing from a page is never called sold. It becomes INACTIVE only when its gift type was
scanned to the end; a listing unseen for STALE_AFTER becomes UNKNOWN.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Gift, Listing
from app.mtproto.parser import ParsedListing
from app.services import cache
from app.utils.log import DATABASE

log = logging.getLogger(DATABASE)

NEW_LISTING = "NEW_LISTING"
PRICE_CHANGED = "PRICE_CHANGED"
LISTING_UPDATED = "LISTING_UPDATED"
LISTING_INACTIVE = "LISTING_INACTIVE"
UNKNOWN_STATE = "UNKNOWN_STATE"

# ponytail: fixed threshold; make it a setting if first-page polling proves too coarse.
STALE_AFTER = timedelta(hours=24)


@dataclass(frozen=True)
class ListingEvent:
    type: str
    listing_id: int
    gift_id: int
    old_price: Decimal | None = None
    new_price: Decimal | None = None
    currency: str | None = None


@dataclass
class IngestStats:
    received: int = 0
    new_gifts: int = 0
    new_listings: int = 0
    duplicates: int = 0
    price_changes: int = 0


def _apply_gift(gift: Gift, p: ParsedListing) -> None:
    gift.title, gift.model, gift.backdrop, gift.pattern = p.title, p.model, p.backdrop, p.pattern
    gift.model_rarity_permille = p.model_rarity_permille
    gift.backdrop_rarity_permille = p.backdrop_rarity_permille
    gift.pattern_rarity_permille = p.pattern_rarity_permille
    gift.availability_issued, gift.availability_total = p.availability_issued, p.availability_total
    gift.burned, gift.crafted, gift.raw_data = p.burned, p.crafted, p.raw_data


def _close(listing: Listing, at: datetime) -> None:
    listing.is_active, listing.state, listing.removed_at = False, "INACTIVE", at


async def _upsert_gift(s: AsyncSession, p: ParsedListing, stats: IngestStats) -> Gift:
    gift = (await s.execute(select(Gift).where(Gift.telegram_unique_id == p.gift_id))).scalar_one_or_none()
    if gift is None:
        gift = Gift(telegram_unique_id=p.gift_id, gift_type_id=p.gift_type_id, slug=p.slug,
                    collection=p.collection, number=p.number, raw_data=p.raw_data)
        s.add(gift)
        stats.new_gifts += 1
    _apply_gift(gift, p)
    await s.flush()
    return gift


async def _new_listing(s: AsyncSession, gift: Gift, p: ParsedListing, at: datetime) -> Listing:
    episode = (await s.execute(select(func.max(Listing.episode)).where(Listing.gift_id == gift.id))).scalar() or 0
    listing = Listing(gift_id=gift.id, listing_identity=f"{p.listing_identity_base}:{episode + 1}",
                      episode=episode + 1, price=p.price, currency=p.currency, price_stars=p.price_stars,
                      price_ton=p.price_ton, seller_name=p.seller_name, state="ACTIVE", is_active=True,
                      listing_date=at, first_seen_at=at, last_seen_at=at, owner_lookup_status="PENDING",
                      raw_data=p.raw_data)
    s.add(listing)
    await s.flush()
    return listing


async def ingest(s: AsyncSession, parsed: list[ParsedListing], seen_at: datetime) -> tuple[list[ListingEvent], IngestStats]:
    """Store every listing of one page; the caller commits. Returns the classified events."""
    events: list[ListingEvent] = []
    stats = IngestStats(received=len(parsed))
    unique = {p.slug: p for p in parsed}  # a page can repeat a gift
    stats.duplicates += len(parsed) - len(unique)
    for p in unique.values():
        gift = await _upsert_gift(s, p, stats)
        active = (await s.execute(
            select(Listing).where(Listing.gift_id == gift.id, Listing.is_active.is_(True)))).scalar_one_or_none()

        if active is not None and p.seller_name and active.seller_name and p.seller_name != active.seller_name:
            _close(active, seen_at)  # another seller: the old episode ended
            await cache.invalidate(s, gift.id, seen_at)  # and the cached owner is someone else now
            await s.flush()
            events.append(ListingEvent(LISTING_INACTIVE, active.id, gift.id))
            active = None

        if active is None:
            listing = await _new_listing(s, gift, p, seen_at)
            events.append(ListingEvent(NEW_LISTING, listing.id, gift.id, new_price=p.price, currency=p.currency))
            stats.new_listings += 1
            continue

        revived = active.state == "UNKNOWN"
        active.last_seen_at, active.state, active.raw_data = seen_at, "ACTIVE", p.raw_data
        active.price_stars, active.price_ton = p.price_stars, p.price_ton  # the converted side drifts
        if p.price is not None and (p.price, p.currency) != (active.price, active.currency):
            old = active.price
            active.price, active.currency = p.price, p.currency
            events.append(ListingEvent(PRICE_CHANGED, active.id, gift.id, old, p.price, p.currency))
            stats.price_changes += 1
        elif revived:
            events.append(ListingEvent(LISTING_UPDATED, active.id, gift.id))
        else:
            stats.duplicates += 1
    await s.flush()
    return events, stats


async def close_missing(s: AsyncSession, gift_type_id: int, seen_slugs: set[str], at: datetime) -> list[ListingEvent]:
    """Only after a gift type was scanned to the last page: active listings not seen are no longer on sale."""
    rows = (await s.execute(select(Listing, Gift.slug).join(Gift).where(
        Gift.gift_type_id == gift_type_id, Listing.is_active.is_(True)))).all()
    events = []
    for listing, slug in rows:
        if slug not in seen_slugs:
            _close(listing, at)
            events.append(ListingEvent(LISTING_INACTIVE, listing.id, listing.gift_id))
    await s.flush()
    return events


async def mark_stale(s: AsyncSession, now: datetime, stale_after: timedelta = STALE_AFTER) -> list[ListingEvent]:
    rows = (await s.execute(select(Listing).where(
        Listing.is_active.is_(True), Listing.state == "ACTIVE", Listing.last_seen_at < now - stale_after))).scalars()
    events = []
    for listing in rows:
        listing.state = "UNKNOWN"
        events.append(ListingEvent(UNKNOWN_STATE, listing.id, listing.gift_id))
    await s.flush()
    return events
