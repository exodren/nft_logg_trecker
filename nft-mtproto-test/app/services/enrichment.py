"""Owner lookup scenario (K4.3), called only for listings that passed stage 1 (§20, F.1).

cache -> budget -> GET /v1/gift/{slug} -> account -> store. Never more than one paid request per gift
within API_CACHE_TTL.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import Gift, Owner
from app.database.types import utcnow
from app.providers.base import (HIDDEN_OWNER_TEXT, Billing, OwnerData, OwnerDataProvider, OwnerStatus,
                                apply_owner_rules)
from app.services import cache
from app.services.api_usage import ApiUsageManager
from app.utils.log import API

log = logging.getLogger(API)


@dataclass(frozen=True)
class Resolution:
    data: OwnerData
    source: str  # cache | api | deferred | disabled
    retry_at: datetime | None = None

    @property
    def deferred(self) -> bool:
        return self.source in ("deferred", "disabled") or self.data.status is OwnerStatus.UNAVAILABLE


class OwnerResolver:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], provider: OwnerDataProvider | None,
                 usage: ApiUsageManager | None, cache_ttl_s: int, clock: Callable[[], datetime] = utcnow) -> None:
        self._sessions = sessions
        self._provider = provider
        self._usage = usage
        self._ttl = cache_ttl_s
        self._clock = clock

    def _unavailable(self, reason: str) -> OwnerData:
        return OwnerData(status=OwnerStatus.UNAVAILABLE, provider=getattr(self._provider, "name", "none"),
                         fetched_at=self._clock(), reason=reason)

    async def resolve(self, gift_id: int) -> Resolution:
        now = self._clock()
        async with self._sessions() as s:
            gift = await s.get(Gift, gift_id)
            if gift is None:
                return Resolution(self._unavailable("gift_missing"), "disabled")
            row = await cache.get_fresh(s, gift_id, now)
            if row is not None:
                owner = await s.get(Owner, row.owner_id) if row.owner_id else None
                return Resolution(cache.to_owner_data(row, owner), "cache")
            slug = gift.slug

        if self._provider is None or not self._provider.ready:
            return Resolution(self._unavailable("provider_disabled"), "disabled")
        if self._usage is not None:
            decision = await self._usage.can_spend()
            if decision.reason == "quota_unknown":
                probe = getattr(self._provider, "probe", None)
                try:
                    billing = await probe() if probe else Billing(free_remaining=None)
                except Exception as e:  # ping failed: do not spend blind
                    log.warning("quota probe failed: %s", e)
                    return Resolution(self._unavailable("quota_unknown"), "deferred")
                if billing.free_remaining is None and probe is not None:
                    return Resolution(self._unavailable("quota_unknown"), "deferred")
                await self._usage.record_probe(billing if probe else Billing(free_remaining=10**9))
                decision = await self._usage.can_spend()
            if not decision.allowed:
                return Resolution(self._unavailable(decision.reason or "budget"), "deferred", decision.retry_at)

        data = apply_owner_rules(await self._provider.lookup(slug))
        if self._usage is not None:
            await self._usage.record(data)
        async with self._sessions.begin() as s:
            await cache.store(s, gift_id, data, self._ttl, self._clock())
        log.info("owner lookup %s: %s%s", slug, data.status.value, f" ({data.reason})" if data.reason else "")
        if data.status is OwnerStatus.HIDDEN:  # shown nowhere in Telegram, kept in the log file
            log.info("%s: %s", slug, HIDDEN_OWNER_TEXT)
        return Resolution(data, "api")
