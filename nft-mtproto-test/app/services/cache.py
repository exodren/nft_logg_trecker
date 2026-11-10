"""Owner lookup cache (K4.8). One row per gift; FOUND/HIDDEN/NOT_FOUND stay fresh for API_CACHE_TTL.

Gift data lives in `gifts`, listing data in `listings`, owner identity in `owners`, and the time of the
last check here: the four are kept apart as §8 K4.8 requires.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Owner, OwnerLookupCache
from app.providers.base import CACHEABLE, Billing, OwnerData, OwnerStatus, apply_owner_rules


# A paid (2xx) answer that could not be used is still not bought twice in a row.
PAID_ERROR_TTL_S = 3600


async def get_fresh(s: AsyncSession, gift_id: int, now: datetime) -> OwnerLookupCache | None:
    """Non-cacheable results are stored with expires_at = now, so freshness alone decides."""
    row = (await s.execute(select(OwnerLookupCache).where(OwnerLookupCache.gift_id == gift_id))).scalar_one_or_none()
    if row is None or row.expires_at <= now:
        return None
    return row


async def invalidate(s: AsyncSession, gift_id: int, now: datetime) -> None:
    """The gift changed hands (new seller): the cached owner is stale."""
    row = (await s.execute(select(OwnerLookupCache).where(OwnerLookupCache.gift_id == gift_id))).scalar_one_or_none()
    if row is not None and row.expires_at > now:
        row.expires_at = now


async def _owner(s: AsyncSession, data: OwnerData, now: datetime) -> Owner | None:
    """An owner record exists only for owners with a username. ID-only, hidden and unknown owners live in
    owner_lookup_cache alone (so the NFT is still not bought twice)."""
    if data.status is not OwnerStatus.FOUND or not data.username:
        return None
    if data.telegram_id is not None:  # the stable key; usernames change
        owner = (await s.execute(select(Owner).where(Owner.telegram_id == data.telegram_id))).scalar_one_or_none()
    else:
        owner = (await s.execute(select(Owner).where(
            Owner.telegram_id.is_(None), Owner.username == data.username))).scalars().first()
    if owner is None:
        owner = Owner(telegram_id=data.telegram_id, source=data.provider, first_seen_at=now, last_seen_at=now)
        s.add(owner)
    owner.username, owner.display_name, owner.last_seen_at = data.username, data.display_name, now
    await s.flush()
    return owner


async def store(s: AsyncSession, gift_id: int, data: OwnerData, ttl_s: int, now: datetime) -> OwnerLookupCache:
    data = apply_owner_rules(data)
    owner = await _owner(s, data, now)
    row = (await s.execute(select(OwnerLookupCache).where(OwnerLookupCache.gift_id == gift_id))).scalar_one_or_none()
    if row is None:
        row = OwnerLookupCache(gift_id=gift_id)
        s.add(row)
    fresh = data.status in CACHEABLE
    paid = data.http_status is not None and 200 <= data.http_status < 300
    row.owner_id = owner.id if owner else None
    row.status, row.is_hidden, row.reason, row.provider = data.status.value, data.is_hidden, data.reason, data.provider
    row.fetched_at, row.last_checked_at = data.fetched_at, now
    if fresh:
        row.expires_at = now + timedelta(seconds=ttl_s)
    elif paid:
        row.expires_at = now + timedelta(seconds=min(PAID_ERROR_TTL_S, ttl_s))
    else:
        row.expires_at = now
    row.cost_ton = data.billing.request_cost if data.billing else None
    row.error_message = None if fresh else data.reason
    row.raw_data = data.raw
    await s.flush()
    return row


def to_owner_data(row: OwnerLookupCache, owner: Owner | None) -> OwnerData:
    return OwnerData(status=OwnerStatus(row.status), provider=row.provider, fetched_at=row.fetched_at,
                     telegram_id=owner.telegram_id if owner else None, username=owner.username if owner else None,
                     display_name=owner.display_name if owner else None, is_hidden=row.is_hidden, reason=row.reason,
                     billing=Billing(request_cost=row.cost_ton) if row.cost_ton is not None else None)
