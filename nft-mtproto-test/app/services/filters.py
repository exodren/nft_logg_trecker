"""Two-stage filtering (§20). Pure functions: no DB, no network.

Stage 1 (F.1) uses only listing characteristics and decides whether an owner lookup is worth paying for.
Stage 2 (F.2) applies owner criteria to the lookup result; without owner data there is no match.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Protocol

from app.providers.base import OwnerData, OwnerStatus


@dataclass(frozen=True)
class ListingView:
    collection: str
    title: str | None
    model: str | None
    backdrop: str | None
    pattern: str | None
    price_stars: Decimal | None
    price_ton: Decimal | None


class FilterLike(Protocol):
    collections: list[str]
    models: list[str]
    backdrops: list[str]
    patterns: list[str]
    price_currency: str
    price_min: Decimal | None
    price_max: Decimal | None
    require_username: bool
    require_telegram_id: bool
    owner_usernames: list[str]
    owner_telegram_ids: list[int]


def norm(value: str | None) -> str:
    """'Ion Gem', 'iongem', 'IonGem' -> 'iongem'."""
    return "".join((value or "").split()).casefold()


def _allowed(value: str | None, wanted: Iterable[str]) -> bool:
    wanted = {norm(w) for w in wanted if w}
    return not wanted or norm(value) in wanted


def stage1(f: FilterLike, v: ListingView) -> bool:
    wanted = {norm(c) for c in f.collections or [] if c}
    if wanted and norm(v.collection) not in wanted and norm(v.title) not in wanted:
        return False
    if not (_allowed(v.model, f.models or []) and _allowed(v.backdrop, f.backdrops or [])
            and _allowed(v.pattern, f.patterns or [])):
        return False
    if f.price_min is None and f.price_max is None:
        return True
    price = v.price_ton if f.price_currency == "TON" else v.price_stars
    if price is None:
        return False
    return (f.price_min is None or price >= f.price_min) and (f.price_max is None or price <= f.price_max)


def norm_username(value: str | None) -> str:
    return (value or "").strip().lstrip("@").casefold()


def has_owner_criteria(f: FilterLike) -> bool:
    return bool(f.require_username or f.require_telegram_id
                or getattr(f, "owner_usernames", None) or getattr(f, "owner_telegram_ids", None))


def stage2(f: FilterLike, owner: OwnerData | None) -> bool:
    """Checks the owner of an already found NFT. No owner criteria: always passes.
    With criteria: only a FOUND owner that satisfies all of them; missing data never matches."""
    if not has_owner_criteria(f):
        return True
    # Every owner criterion needs a username: an ID alone is not a usable owner (NO_USERNAME never matches).
    if owner is None or owner.status is not OwnerStatus.FOUND or not owner.username:
        return False
    if f.require_telegram_id and owner.telegram_id is None:
        return False
    usernames = {norm_username(u) for u in getattr(f, "owner_usernames", None) or [] if u}
    if usernames and norm_username(owner.username) not in usernames:
        return False  # also when the owner has no username: absence is not a match
    ids = {int(i) for i in getattr(f, "owner_telegram_ids", None) or []}
    if ids and owner.telegram_id not in ids:
        return False
    return True
