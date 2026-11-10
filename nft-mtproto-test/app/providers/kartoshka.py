"""KartoshkaApiProvider: official kartoshka.free API (research/data/reports/kartoshka_api_integration.md).

Only documented behaviour is used: GET /v1/gift/{ref}, Bearer token, {ok, result} envelope, codes
400/401/402/404/429/500, Retry-After, X-Free-Remaining / X-Request-Cost / X-Balance, GET /v1/pricing.
The JSON schema of /v1/gift is not published. Owner fields are read by an OwnerMapper; until a mapper is
verified against real responses (task K4.1b) the provider reports ready=False and is not called.
/who and /owners/search are deliberately absent (§2.2).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import quote

import aiohttp

from app.database.types import utcnow
from app.providers.base import Billing, OwnerData, OwnerStatus
from app.utils.log import API

log = logging.getLogger(API)

BASE_URL = "https://kartoshka.free/v1"
TIMEOUT_S = 15
MAX_RETRIES = 3
MAX_RETRY_AFTER_S = 60
# ponytail: 1 request/s is the documented floor (no balance); our 3-7k/day is ~0.1 rps. Raise if load grows.
MIN_INTERVAL_S = 1.0


@dataclass(frozen=True)
class OwnerFields:
    status: OwnerStatus
    telegram_id: int | None = None
    username: str | None = None
    display_name: str | None = None
    is_hidden: bool = False
    reason: str | None = None


class OwnerMapper(Protocol):
    verified: bool

    def __call__(self, result: dict[str, Any]) -> OwnerFields: ...


class UnverifiedSchemaMapper:
    """No field names are guessed. The raw result is kept so the mapping can be written from real data."""
    verified = False

    def __call__(self, result: dict[str, Any]) -> OwnerFields:
        return OwnerFields(OwnerStatus.UNAVAILABLE, reason="schema_unverified")


@dataclass(frozen=True)
class Pricing:
    price_per_request: Decimal
    free_per_day: int
    raw: dict[str, Any]


def parse_billing(headers: Any) -> Billing:
    def dec(name: str) -> Decimal | None:
        try:
            return Decimal(headers[name]) if headers.get(name) not in (None, "") else None
        except (InvalidOperation, TypeError):
            return None
    free = headers.get("X-Free-Remaining")
    return Billing(free_remaining=int(free) if free is not None and str(free).isdigit() else None,
                   request_cost=dec("X-Request-Cost"), balance=dec("X-Balance"))


def _retry_after(headers: Any, attempt: int) -> float:
    try:
        return min(float(headers.get("Retry-After", "")), MAX_RETRY_AFTER_S)
    except ValueError:
        return min(2.0 ** attempt, MAX_RETRY_AFTER_S)


class KartoshkaApiProvider:
    name = "kartoshka"

    def __init__(self, token: str, *, base_url: str = BASE_URL, max_concurrency: int = 2,
                 mapper: OwnerMapper | None = None, session: aiohttp.ClientSession | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, min_interval_s: float = MIN_INTERVAL_S,
                 clock: Callable[[], datetime] = utcnow) -> None:
        if not token:
            raise ValueError("KARTOSHKA_API_TOKEN is empty")
        self._token = token
        self._base = base_url.rstrip("/")
        self._mapper = mapper or UnverifiedSchemaMapper()
        self._session = session
        self._own_session = session is None
        self._sem = asyncio.Semaphore(max_concurrency)
        self._sleep = sleep
        self._interval = min_interval_s
        self._next_slot = 0.0
        self._slot_lock = asyncio.Lock()
        self._clock = clock
        self.auth_failed = False

    @property
    def ready(self) -> bool:
        return self._mapper.verified and not self.auth_failed

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
                connector=aiohttp.TCPConnector(limit=10))  # pooled keep-alive connections
        return self._session

    async def close(self) -> None:
        if self._session is not None and self._own_session:
            await self._session.close()
        self._session = None

    async def _pace(self) -> None:
        async with self._slot_lock:
            now = time.monotonic()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self._interval
        if wait > 0:
            await self._sleep(wait)

    async def get(self, path: str) -> tuple[int, Any, Any]:
        """Low-level GET: (status, json body or None, headers). Used by lookup, pricing and fixture capture."""
        http = await self._http()
        await self._pace()
        async with http.get(f"{self._base}{path}", headers={"Authorization": f"Bearer {self._token}"}) as resp:
            try:
                body = await resp.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError):
                body = None
            return resp.status, body, resp.headers

    async def probe(self) -> Billing:
        """GET /v1/ping is never charged; its headers tell the shared free quota and the balance."""
        status, _, headers = await self.get("/ping")
        if status == 401:
            self.auth_failed = True
        if status != 200:
            raise RuntimeError(f"/v1/ping failed: HTTP {status}")
        return parse_billing(headers)

    async def pricing(self) -> Pricing:
        status, body, _ = await self.get("/pricing")
        if status != 200 or not isinstance(body, dict) or not body.get("ok"):
            raise RuntimeError(f"/v1/pricing failed: HTTP {status}")
        r = body["result"]
        return Pricing(Decimal(str(r["pricePerRequest"])), int(r["freePerDay"]), r)

    def _result(self, status: OwnerStatus, *, http: int | None = None, billing: Billing | None = None,
                reason: str | None = None, raw: dict | None = None, fields: OwnerFields | None = None) -> OwnerData:
        f = fields or OwnerFields(status, reason=reason)
        return OwnerData(status=f.status, provider=self.name, fetched_at=self._clock(), telegram_id=f.telegram_id,
                         username=f.username, display_name=f.display_name, is_hidden=f.is_hidden,
                         reason=f.reason or reason, http_status=http, billing=billing, raw=raw)

    async def lookup(self, ref: str) -> OwnerData:
        path = f"/gift/{quote(ref, safe='')}"
        async with self._sem:
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    status, body, headers = await self.get(path)
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    log.warning("kartoshka %s: %s, attempt %s/%s", ref, type(e).__name__, attempt, MAX_RETRIES)
                    if attempt == MAX_RETRIES:
                        return self._result(OwnerStatus.UNAVAILABLE, reason="network")
                    await self._sleep(2.0 ** attempt)
                    continue
                billing = parse_billing(headers)
                raw = body if isinstance(body, dict) else None
                if status == 200:
                    if not raw or raw.get("ok") is not True or not isinstance(raw.get("result"), dict):
                        return self._result(OwnerStatus.ERROR, http=status, billing=billing, reason="malformed", raw=raw)
                    return self._result(OwnerStatus.ERROR, http=status, billing=billing, raw=raw,
                                        fields=self._mapper(raw["result"]))
                if status == 404:
                    return self._result(OwnerStatus.NOT_FOUND, http=status, billing=billing, raw=raw)
                if status == 402:
                    log.error("kartoshka: 402 payment required (free quota and balance exhausted)")
                    return self._result(OwnerStatus.UNAVAILABLE, http=status, billing=billing,
                                        reason="payment_required", raw=raw)
                if status == 401:
                    self.auth_failed = True
                    log.error("kartoshka: 401, token missing, unknown or reset; lookups stopped")
                    return self._result(OwnerStatus.ERROR, http=status, reason="unauthorized", raw=raw)
                if status == 400:
                    return self._result(OwnerStatus.ERROR, http=status, reason="bad_request", raw=raw)
                retryable = status == 429 or status >= 500
                reason = "rate_limited" if status == 429 else f"http_{status}"
                log.warning("kartoshka %s: HTTP %s, attempt %s/%s", ref, status, attempt, MAX_RETRIES)
                if not retryable or attempt == MAX_RETRIES:
                    return self._result(OwnerStatus.UNAVAILABLE, http=status, billing=billing, reason=reason, raw=raw)
                await self._sleep(_retry_after(headers, attempt) if status == 429 else 2.0 ** attempt)
        return self._result(OwnerStatus.UNAVAILABLE, reason="retries_exhausted")
