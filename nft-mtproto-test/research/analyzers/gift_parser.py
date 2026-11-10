"""Pure functions over raw JSON dicts, so results can be re-derived without new API calls."""
from __future__ import annotations

from collections import Counter
from typing import Any

TON_NANO = 1_000_000_000


def peer_type(peer: dict | None) -> str | None:
    return peer.get("_") if peer else None


def peer_id(peer: dict | None) -> int | None:
    if not peer:
        return None
    return peer.get("user_id") or peer.get("channel_id") or peer.get("chat_id")


def parse_prices(resell_amount: list[dict] | None) -> list[dict]:
    """StarsAmount -> XTR; StarsTonAmount -> TON (the API sends nanotons)."""
    out = []
    for a in resell_amount or []:
        if a.get("_") == "StarsTonAmount":
            out.append({"amount": a["amount"] / TON_NANO, "currency": "TON"})
        else:
            out.append({"amount": a.get("amount"), "currency": "XTR"})
    return out


def attribute(gift: dict, tl_name: str) -> str | None:
    return next((a.get("name") for a in gift.get("attributes", []) if a.get("_") == tl_name), None)


def extract_listing(gift: dict) -> dict:
    """Flat view. owner, host and wallet deliberately stay separate fields."""
    return {
        "id": gift.get("id"), "gift_id": gift.get("gift_id"), "title": gift.get("title"),
        "slug": gift.get("slug"), "num": gift.get("num"),
        "owner_peer_type": peer_type(gift.get("owner_id")), "owner_peer_id": peer_id(gift.get("owner_id")),
        "owner_name": gift.get("owner_name"), "owner_address": gift.get("owner_address"),
        "host_peer_type": peer_type(gift.get("host_id")), "host_peer_id": peer_id(gift.get("host_id")),
        "gift_address": gift.get("gift_address"),
        "prices": parse_prices(gift.get("resell_amount")),
        "resale_ton_only": bool(gift.get("resale_ton_only")),
        "availability_issued": gift.get("availability_issued"),
        "availability_total": gift.get("availability_total"),
        "model": attribute(gift, "StarGiftAttributeModel"),
        "pattern": attribute(gift, "StarGiftAttributePattern"),
        "backdrop": attribute(gift, "StarGiftAttributeBackdrop"),
    }


def field_inventory(gifts: list[dict]) -> dict[str, dict[str, Any]]:
    """Per TL field: fill rate and value types. Examples are omitted: they may be personal data."""
    seen: dict[str, Counter] = {}
    for g in gifts:
        for key, value in g.items():
            kind = (value.get("_") if isinstance(value, dict) else None) or type(value).__name__
            seen.setdefault(key, Counter())[kind] += 1
    total = len(gifts) or 1
    return {
        k: {"present": sum(c.values()) - c["NoneType"], "fill_rate": round((sum(c.values()) - c["NoneType"]) / total, 4),
            "types": dict(c)}
        for k, c in sorted(seen.items())
    }


def peer_references(gift: dict) -> dict[str, int]:
    """Every field of a gift that points at a user/chat, to explain result.users contents."""
    refs = {}
    for field in ("owner_id", "host_id", "released_by", "theme_peer"):
        pid = peer_id(gift.get(field))
        if pid is not None:
            refs[field] = pid
    for a in gift.get("attributes", []):
        if a.get("_") == "StarGiftAttributeOriginalDetails":
            for field in ("sender_id", "recipient_id"):
                pid = peer_id(a.get(field))
                if pid is not None:
                    refs[f"original_{field}"] = pid
    return refs


def price_key(gift: dict) -> tuple[str, int | None]:
    """The amount the seller actually set. The other currency is a live conversion that drifts with the rate."""
    wanted = "StarsTonAmount" if gift.get("resale_ton_only") else "StarsAmount"
    amount = next((a.get("amount") for a in gift.get("resell_amount") or [] if a.get("_") == wanted), None)
    return wanted, amount
