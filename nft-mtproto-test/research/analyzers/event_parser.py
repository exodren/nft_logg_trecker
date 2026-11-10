"""Normalize raw gifts to one event format and derive listing events from snapshots."""
from __future__ import annotations

from .gift_parser import extract_listing, peer_id
from .peer_resolver import primary_username


def _lookup(peer: dict | None, users: list[dict]) -> dict | None:
    pid = peer_id(peer)
    return next((u for u in users if u.get("_") == "User" and u.get("id") == pid), None) if pid else None


def _party(peer: dict | None, users: list[dict]) -> dict:
    user = _lookup(peer, users)
    return {"telegram_id": peer_id(peer) if (peer or {}).get("_") == "PeerUser" else None,
            "username": primary_username(user) if user else None}


def normalize_event(event_type: str, gift: dict, users: list[dict], source: str = "telegram_mtproto",
                    timestamp: str | None = None, raw_data_file: str | None = None,
                    owner_is_seller: bool = False) -> dict:
    """seller stays unknown unless the caller has evidence that owner is the seller."""
    flat = extract_listing(gift)
    owner = _party(gift.get("owner_id"), users)
    owner_known = owner["telegram_id"] is not None
    seller = {"telegram_id": None, "username": None, "display_name": None, "resolution_status": "unknown"}
    if owner_is_seller and owner_known:
        seller = {**owner, "display_name": flat["owner_name"], "resolution_status": "owner_id"}
    price = flat["prices"][0] if flat["prices"] else {"amount": None, "currency": None}
    return {
        "event_type": event_type, "source": source,
        "nft": {"slug": flat["slug"], "gift_id": str(flat["gift_id"]), "nft_id": str(flat["id"]),
                "gift_address": flat["gift_address"]},
        "seller": seller,
        "owner": {**owner, "display_name": flat["owner_name"], "ton_address": flat["owner_address"],
                  "resolution_status": "owner_id" if owner_known else "unknown"},
        "host": _party(gift.get("host_id"), users),
        "price": price, "timestamp": timestamp, "raw_data_file": raw_data_file,
    }


def diff_snapshots(previous: dict[str, list], current: dict[str, list],
                   ever_seen: frozenset[str] = frozenset()) -> list[tuple[str, str]]:
    """Compare {slug: gift_parser.price_key(gift)} of COMPLETE scans.

    Raw resell_amount must not be passed: its converted currency drifts and fakes price changes.
    A partial scan must not be passed either: absence from it means nothing.
    """
    events = []
    for slug, prices in current.items():
        if slug not in previous:
            events.append(("RELIST" if slug in ever_seen else "LISTING", slug))
        elif previous[slug] != prices:
            events.append(("PRICE_CHANGE", slug))
    events += [("DELIST_OR_SALE", slug) for slug in previous if slug not in current]
    return events
