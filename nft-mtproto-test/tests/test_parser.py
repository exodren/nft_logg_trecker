import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.mtproto.parser import ParseError, parse_listing, parse_page, split_slug

FIXTURES = Path(__file__).parent / "fixtures" / "resale"
RESEARCH_DUMPS = Path(__file__).resolve().parents[1] / "research" / "data" / "raw" / "resale"
NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)


def page(name: str = "5843762284240831056_p0.json") -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def first_gift() -> dict:
    return next(g for g in page()["gifts"] if g["slug"] == "IonGem-3185")


@pytest.mark.parametrize("slug,expected", [
    ("IonGem-3185", ("IonGem", 3185)),
    ("MoonPendant-71710", ("MoonPendant", 71710)),
    ("Some-Hyphen-Gift-7", ("Some-Hyphen-Gift", 7)),
])
def test_split_slug(slug, expected):
    assert split_slug(slug) == expected


@pytest.mark.parametrize("slug", ["IonGem", "IonGem-", "-12", "IonGem-12a", None, ""])
def test_bad_slug_rejected(slug):
    with pytest.raises(ParseError):
        split_slug(slug)


def test_real_listing_fields():
    p = parse_listing(first_gift(), NOW)
    assert (p.slug, p.collection, p.number, p.title) == ("IonGem-3185", "IonGem", 3185, "Ion Gem")
    assert p.price_stars == Decimal(8755) and p.price_ton == Decimal("123.24")
    assert (p.price, p.currency) == (Decimal(8755), "XTR")
    assert p.model and p.backdrop and p.pattern
    assert p.listing_date == NOW and p.raw_data["slug"] == "IonGem-3185"


def test_big_ids_stay_exact_ints():
    p = parse_listing(first_gift(), NOW)
    assert isinstance(p.gift_id, int) and p.gift_id > 2**53
    assert p.gift_type_id == 5843762284240831056


def test_ton_only_listing_uses_ton_price():
    g = {**first_gift(), "resale_ton_only": True}
    p = parse_listing(g, NOW)
    assert (p.price, p.currency) == (Decimal("123.24"), "TON")


def test_ton_precision_is_not_lost():
    g = {**first_gift(), "resell_amount": [{"_": "StarsTonAmount", "amount": 123240000001}], "resale_ton_only": True}
    assert parse_listing(g, NOW).price == Decimal("123.240000001")


def test_missing_price_attributes_and_unknowns():
    g = {"_": "StarGiftUnique", "id": 1, "gift_id": 2, "slug": "X-5", "attributes": [{"_": "StarGiftAttributeNew"}],
         "resell_amount": [{"_": "StarsFutureAmount", "amount": 1}], "unknown_field": {"a": 1}}
    p = parse_listing(g, NOW)
    assert p.price is None and p.currency is None and p.model is None and p.pattern is None and p.backdrop is None
    assert p.raw_data["unknown_field"] == {"a": 1}


@pytest.mark.parametrize("change", [
    {"_": "StarGift"}, {"id": None}, {"slug": "NoNumber"}, {"num": 9999},
])
def test_invalid_objects_rejected(change):
    with pytest.raises(ParseError):
        parse_listing({**first_gift(), **change}, NOW)


def test_bad_values_do_not_crash():
    g = {**first_gift(), "resell_amount": [{"_": "StarsAmount", "amount": "abc"}, "junk"], "availability_total": "x",
         "attributes": ["junk", {"_": "StarGiftAttributeModel", "name": 5, "rarity": "bad"}]}
    p = parse_listing(g, NOW)
    assert p.price_stars is None and p.availability_total is None and p.model is None


def test_page_skips_bad_items_and_keeps_offset():
    raw = page()
    raw["gifts"] = raw["gifts"][:3] + [{"_": "StarGift", "id": 1}]
    result = parse_page(raw, NOW)
    assert len(result.listings) == 3 and len(result.errors) == 1
    assert result.next_offset == raw["next_offset"] and result.count == raw["count"]


def test_seller_name_is_kept_as_text_only():
    p = parse_listing({**first_gift(), "owner_name": "@someone"}, NOW)
    assert p.seller_name == "@someone"


@pytest.mark.skipif(not RESEARCH_DUMPS.exists(), reason="research dumps are local only")
def test_all_research_dumps_parse():
    total = 0
    for path in RESEARCH_DUMPS.glob("*.json"):
        result = parse_page(json.loads(path.read_text(encoding="utf-8")), NOW)
        assert result.errors == [], path.name
        total += len(result.listings)
    assert total == 2250
