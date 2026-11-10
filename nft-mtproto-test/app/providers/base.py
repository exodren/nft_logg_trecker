"""Owner data contract shared by the whole app (K4.2, K4.4)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol


class OwnerStatus(StrEnum):
    FOUND = "FOUND"  # owner with a public username (after apply_owner_rules)
    NO_USERNAME = "NO_USERNAME"  # owner exists but has no username: not useful, no owner record is kept
    HIDDEN = "HIDDEN"  # owner hid the profile; never worked around
    NOT_FOUND = "NOT_FOUND"  # gift or owner absent in the provider
    UNAVAILABLE = "UNAVAILABLE"  # temporary: network, 429, 5xx, 402, budget, unverified schema
    ERROR = "ERROR"  # bad request, bad token, malformed response


# Written to the log only; a hidden owner is never mentioned in Telegram messages.
HIDDEN_OWNER_TEXT = "🔒 Владелец скрыл профиль"

# Final answers that are worth caching; UNAVAILABLE and ERROR are retried later.
CACHEABLE = frozenset({OwnerStatus.FOUND, OwnerStatus.NO_USERNAME, OwnerStatus.HIDDEN, OwnerStatus.NOT_FOUND})


@dataclass(frozen=True)
class Billing:
    """From X-Free-Remaining / X-Request-Cost / X-Balance (kartoshka_api_integration.md §5)."""
    free_remaining: int | None = None
    request_cost: Decimal | None = None
    balance: Decimal | None = None


@dataclass(frozen=True)
class OwnerData:
    status: OwnerStatus
    provider: str
    fetched_at: datetime
    telegram_id: int | None = None
    username: str | None = None  # never a display name
    display_name: str | None = None
    is_hidden: bool = False
    reason: str | None = None
    http_status: int | None = None
    billing: Billing | None = None
    raw: dict[str, Any] | None = None

    @property
    def has_username(self) -> bool:
        return self.status is OwnerStatus.FOUND and bool(self.username)

    @property
    def has_telegram_id(self) -> bool:
        return self.status is OwnerStatus.FOUND and self.telegram_id is not None


def apply_owner_rules(data: OwnerData) -> OwnerData:
    """A Telegram ID without a username cannot be used to reach the profile, so such an owner is kept only
    as a cached NO_USERNAME result: no ID, no owner record. Nothing tries to find the username elsewhere."""
    if data.status is OwnerStatus.FOUND and not (data.username or "").strip():
        return replace(data, status=OwnerStatus.NO_USERNAME, telegram_id=None, username=None,
                       reason=data.reason or "no_username")
    return data


class OwnerDataProvider(Protocol):
    name: str

    @property
    def ready(self) -> bool:
        """False when the provider must not be called (no token, unverified response mapping, auth failure)."""

    async def lookup(self, ref: str) -> OwnerData:
        """ref is the gift slug 'Collection-Number'. Never raises for HTTP/API errors: they become a status."""
