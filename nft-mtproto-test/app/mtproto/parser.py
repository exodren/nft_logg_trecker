"""MTProto StarGiftUnique -> normalized listing. Pure functions, no network.

Ported from research/analyzers/gift_parser.py with Decimal instead of float for TON.
listing_identity needs the sale episode, which only the database knows: the parser gives `slug` as the
identity base and app/services/listings.py builds '{slug}:{episode}'. listing_date is the detection time:
StarGiftUnique carries no listing date (see project_audit.md).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

NANO = Decimal(1_000_000_000)
XTR = "XTR"
TON = "TON"


class ParseError(ValueError):
    """The object cannot be a listing (wrong type, no slug/number/id)."""


@dataclass(frozen=True)
class ParsedListing:
    gift_id: int  # StarGiftUnique.id, unique per NFT
    gift_type_id: int  # StarGiftUnique.gift_id, the collection type
    slug: str
    collection: str
    number: int
    title: str | None
    price: Decimal | None  # in the seller's currency
    currency: str | None
    price_stars: Decimal | None
    price_ton: Decimal | None
    model: str | None
    backdrop: str | None
    pattern: str | None
    model_rarity_permille: int | None
    backdrop_rarity_permille: int | None
    pattern_rarity_permille: int | None
    availability_issued: int | None
    availability_total: int | None
    burned: bool
    crafted: bool
    seller_name: str | None  # owner_name: a display name typed by the user, never a username
    listing_date: datetime
    raw_data: dict[str, Any]

    @property
    def listing_identity_base(self) -> str:
        return self.slug


def split_slug(slug: str) -> tuple[str, int]:
    """'MoonPendant-71710' -> ('MoonPendant', 71710). Splits on the last hyphen only."""
    if not isinstance(slug, str) or "-" not in slug:
        raise ParseError(f"slug without number: {slug!r}")
    collection, number = slug.rsplit("-", 1)
    if not collection or not number.isdigit():
        raise ParseError(f"slug without number: {slug!r}")
    return collection, int(number)


def to_raw(obj: Any) -> dict[str, Any]:
    """TL object or dict -> JSON-safe dict (dates and bytes become strings, ints stay exact)."""
    data = obj.to_dict() if hasattr(obj, "to_dict") else obj
    if not isinstance(data, dict):
        raise ParseError(f"unexpected object: {type(obj).__name__}")
    return json.loads(json.dumps(data, default=str, ensure_ascii=False))


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _amount(item: dict[str, Any]) -> Decimal | None:
    amount = _int(item.get("amount"))
    if amount is None or amount < 0:
        return None
    if item.get("_") == "StarsTonAmount":
        return Decimal(amount) / NANO  # nanotons
    nanos = _int(item.get("nanos")) or 0
    return Decimal(amount) + Decimal(nanos) / NANO


def parse_prices(resell_amount: Any) -> tuple[Decimal | None, Decimal | None]:
    """(stars, ton). Unknown amount types are ignored; raw_data keeps them."""
    stars = ton = None
    for item in resell_amount if isinstance(resell_amount, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("_") == "StarsAmount":
            stars = _amount(item)
        elif item.get("_") == "StarsTonAmount":
            ton = _amount(item)
    return stars, ton


def _attribute(attributes: list[Any], tl_name: str) -> tuple[str | None, int | None]:
    for a in attributes:
        if isinstance(a, dict) and a.get("_") == tl_name:
            rarity = a.get("rarity")
            permille = _int(rarity.get("permille")) if isinstance(rarity, dict) else None
            name = a.get("name")
            return (name if isinstance(name, str) else None), permille
    return None, None


def parse_listing(obj: Any, seen_at: datetime) -> ParsedListing:
    raw = to_raw(obj)
    if raw.get("_") != "StarGiftUnique":
        raise ParseError(f"not a unique gift: {raw.get('_')!r}")
    gift_id, gift_type_id = _int(raw.get("id")), _int(raw.get("gift_id"))
    if gift_id is None or gift_type_id is None:
        raise ParseError("missing id or gift_id")
    collection, number = split_slug(raw.get("slug"))
    num = _int(raw.get("num"))
    if num is not None and num != number:
        raise ParseError(f"slug number {number} != num {num}")

    stars, ton = parse_prices(raw.get("resell_amount"))
    ton_only = bool(raw.get("resale_ton_only"))
    price, currency = (ton, TON) if ton_only else (stars, XTR)
    if price is None:
        currency = None
    attributes = raw.get("attributes") if isinstance(raw.get("attributes"), list) else []
    model, model_r = _attribute(attributes, "StarGiftAttributeModel")
    backdrop, backdrop_r = _attribute(attributes, "StarGiftAttributeBackdrop")
    pattern, pattern_r = _attribute(attributes, "StarGiftAttributePattern")
    seller = raw.get("owner_name")

    return ParsedListing(
        gift_id=gift_id, gift_type_id=gift_type_id, slug=raw["slug"], collection=collection, number=number,
        title=raw.get("title") if isinstance(raw.get("title"), str) else None,
        price=price, currency=currency, price_stars=stars, price_ton=ton,
        model=model, backdrop=backdrop, pattern=pattern,
        model_rarity_permille=model_r, backdrop_rarity_permille=backdrop_r, pattern_rarity_permille=pattern_r,
        availability_issued=_int(raw.get("availability_issued")), availability_total=_int(raw.get("availability_total")),
        burned=bool(raw.get("burned")), crafted=bool(raw.get("crafted")),
        seller_name=seller if isinstance(seller, str) else None,
        listing_date=seen_at, raw_data=raw,
    )


@dataclass(frozen=True)
class ParsedPage:
    listings: list[ParsedListing]
    errors: list[str]
    next_offset: str | None
    count: int | None


def parse_page(result: Any, seen_at: datetime) -> ParsedPage:
    """payments.ResaleStarGifts -> listings. One bad item never drops the page. result.users are not sellers."""
    raw = to_raw(result)
    listings, errors = [], []
    for item in raw.get("gifts") or []:
        try:
            listings.append(parse_listing(item, seen_at))
        except ParseError as e:
            errors.append(str(e))
    next_offset = raw.get("next_offset")
    return ParsedPage(listings, errors, next_offset if isinstance(next_offset, str) and next_offset else None,
                      _int(raw.get("count")))
