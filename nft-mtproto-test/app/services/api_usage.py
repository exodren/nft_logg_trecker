"""API Usage Manager (K4.7): per-UTC-day counters, free quota, real cost, budget, admin warnings, stop.

Costs come from the response headers (X-Request-Cost, X-Free-Remaining, X-Balance); the price from
/v1/pricing is only used to predict whether the next paid request fits the budget. Nothing is hard-coded.
DAILY_API_BUDGET_TON = 0 means: free quota only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import ApiUsage
from app.database.types import utcnow
from app.providers.base import Billing, OwnerData, OwnerStatus
from app.utils.log import API

log = logging.getLogger(API)

WARN_SHARE = Decimal("0.8")
Notify = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None = None
    retry_at: datetime | None = None


@dataclass(frozen=True)
class UsageReport:
    day: date
    requests: int
    success: int
    errors: int
    free: int
    paid: int
    spent_ton: Decimal
    budget_ton: Decimal
    remaining_budget_ton: Decimal
    average_cost_ton: Decimal | None
    free_remaining: int | None
    balance_ton: Decimal | None
    stopped_reason: str | None


def next_utc_midnight(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=timezone.utc)


class ApiUsageManager:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], provider: str, budget_ton: Decimal,
                 price_per_request: Decimal | None = None, notify_admin: Notify | None = None,
                 clock: Callable[[], datetime] = utcnow) -> None:
        self._sessions = sessions
        self._provider = provider
        self.budget = budget_ton
        self.price_per_request = price_per_request
        self._notify = notify_admin
        self._clock = clock

    async def _row(self, s: AsyncSession) -> ApiUsage:
        day = self._clock().astimezone(timezone.utc).date()
        row = (await s.execute(select(ApiUsage).where(
            ApiUsage.provider == self._provider, ApiUsage.day == day))).scalar_one_or_none()
        if row is None:
            row = ApiUsage(provider=self._provider, day=day, requests_total=0, requests_success=0, requests_error=0,
                           free_requests=0, paid_requests=0, spent_ton=Decimal("0"), budget_warning_sent=False)
            s.add(row)
            await s.flush()
        return row

    async def can_spend(self) -> Decision:
        now = self._clock()
        async with self._sessions.begin() as s:
            row = await self._row(s)
            if row.paid_stopped_reason == "unauthorized":
                return Decision(False, "unauthorized")
            # The free quota is shared by all apps of the account: learn it (free /v1/ping) before spending.
            if row.last_free_remaining is None:
                return Decision(False, "quota_unknown")
            if row.last_free_remaining > 0:
                row.last_free_remaining -= 1  # reserve; the response header restores the true value
                return Decision(True)
            if row.paid_stopped_reason:
                return Decision(False, row.paid_stopped_reason, next_utc_midnight(now))
            if self.budget <= 0:
                return Decision(False, "free_quota_exhausted", next_utc_midnight(now))
            price = self.price_per_request
            if price is None or row.spent_ton + price > self.budget:
                return Decision(False, "budget_exhausted", next_utc_midnight(now))
            return Decision(True)

    async def record_probe(self, billing: Billing) -> None:
        """Headers of a free service call (/v1/ping): quota and balance, no request counted."""
        async with self._sessions.begin() as s:
            row = await self._row(s)
            if billing.free_remaining is not None:
                row.last_free_remaining = billing.free_remaining
            if billing.balance is not None:
                row.last_balance_ton = billing.balance

    async def record(self, data: OwnerData) -> None:
        """Account one provider response. Only 2xx are charged (404 and errors are free)."""
        messages: list[str] = []
        async with self._sessions.begin() as s:
            row = await self._row(s)
            row.requests_total += 1
            charged = data.http_status is not None and 200 <= data.http_status < 300
            if charged:
                row.requests_success += 1
            elif data.status in (OwnerStatus.ERROR, OwnerStatus.UNAVAILABLE):
                row.requests_error += 1
            b = data.billing
            if b is not None:
                if b.free_remaining is not None:
                    row.last_free_remaining = b.free_remaining
                if b.balance is not None:
                    row.last_balance_ton = b.balance
            if charged:
                cost = b.request_cost if b and b.request_cost is not None else None
                if cost is None:  # no header: assume free while quota is known to remain
                    cost = Decimal("0") if (row.last_free_remaining or 0) > 0 else (self.price_per_request or Decimal("0"))
                if cost > 0:
                    row.paid_requests += 1
                    row.spent_ton += cost
                else:
                    row.free_requests += 1
            if data.reason == "payment_required" and row.paid_stopped_reason != "payment_required":
                row.paid_stopped_reason = "payment_required"
                row.last_free_remaining = 0
                messages.append("Kartoshka API: 402 — бесплатные запросы закончились и баланса не хватает. "
                                "Платные запросы остановлены до 00:00 UTC или пополнения.")
            if data.reason == "unauthorized" and row.paid_stopped_reason != "unauthorized":
                row.paid_stopped_reason = "unauthorized"
                messages.append("Kartoshka API: 401 — токен не принят. Запросы остановлены, проверьте KARTOSHKA_API_TOKEN.")
            if self.budget > 0 and not row.budget_warning_sent and row.spent_ton >= self.budget * WARN_SHARE:
                row.budget_warning_sent = True
                messages.append(f"Kartoshka API: потрачено {row.spent_ton} из {self.budget} TON дневного бюджета.")
            if self.budget > 0 and row.spent_ton >= self.budget and not row.paid_stopped_reason:
                row.paid_stopped_reason = "budget_exhausted"
                messages.append("Kartoshka API: дневной бюджет исчерпан, платные запросы остановлены до 00:00 UTC.")
        for m in messages:
            log.warning(m)
            if self._notify:
                await self._notify(m)

    async def resume(self) -> None:
        """Admin action after a top-up or a budget change."""
        async with self._sessions.begin() as s:
            row = await self._row(s)
            row.paid_stopped_reason = None
            row.last_free_remaining = None

    async def report(self) -> UsageReport:
        async with self._sessions.begin() as s:
            r = await self._row(s)
            avg = (r.spent_ton / r.paid_requests) if r.paid_requests else None
            return UsageReport(r.day, r.requests_total, r.requests_success, r.requests_error, r.free_requests,
                               r.paid_requests, r.spent_ton, self.budget, max(self.budget - r.spent_ton, Decimal("0")),
                               avg, r.last_free_remaining, r.last_balance_ton, r.paid_stopped_reason)
