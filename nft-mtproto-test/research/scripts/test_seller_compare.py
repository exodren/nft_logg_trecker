"""Priority 1: fresh listing vs getUniqueStarGift for the same slug, compared field by field.

Bounded: GIFT_TYPES first pages + SAMPLE_PER_TYPE cards each. Never looks up any account.
Peers are only matched by ID against what the NFT object itself references.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from _common import config
from analyzers.gift_parser import peer_references
from mtproto_client import Api, open_api, record_error, run_script, to_jsonable, write_json
from telethon.errors import RPCError
from telethon.tl.functions.payments import (GetResaleStarGiftsRequest, GetStarGiftsRequest,
                                            GetUniqueStarGiftRequest)

GIFT_TYPES = 5
PAGE_SIZE = 50
SAMPLE_PER_TYPE = 10
PEER_FIELDS = ("owner_id", "host_id", "owner_address", "owner_name", "offer_min_stars", "gift_address", "released_by")
OUT = config.RAW_DIR / "seller_discovery"
# ponytail: prices in the non-seller currency drift between two calls; such diffs are reported, not filtered.


def deep_diff(listing: Any, card: Any, path: str = "") -> dict[str, list]:
    """Every path whose value differs, including nested objects and lists."""
    if isinstance(listing, dict) and isinstance(card, dict):
        out: dict[str, list] = {}
        for key in sorted(listing.keys() | card.keys()):
            out |= deep_diff(listing.get(key), card.get(key), f"{path}.{key}" if path else key)
        return out
    if isinstance(listing, list) and isinstance(card, list) and len(listing) == len(card):
        out = {}
        for i, (a, b) in enumerate(zip(listing, card)):
            out |= deep_diff(a, b, f"{path}[{i}]")
        return out
    return {} if listing == card else {path: [listing, card]}


def peer_ids(objects: list[dict]) -> set[int]:
    return {o["id"] for o in objects if o.get("id") is not None}


def linkage(gift: dict, users: list[dict], chats: list[dict]) -> dict:
    """Which returned peers the NFT object actually points at, by which field."""
    refs = peer_references(gift)
    returned = peer_ids(users) | peer_ids(chats)
    referenced = set(refs.values())
    return {"reference_fields": sorted(refs), "returned": len(returned),
            "referenced_and_returned": len(returned & referenced),
            "returned_unreferenced": len(returned - referenced)}


def summarize(records: list[dict]) -> dict:
    fields: dict[str, Counter] = {f: Counter() for f in PEER_FIELDS}
    diff_paths: Counter = Counter()
    ref_fields: Counter = Counter()
    offer_vs_owner: Counter = Counter()
    for r in records:
        for f in PEER_FIELDS:
            listed, carded = r["listing"].get(f) is not None, r["card"].get(f) is not None
            fields[f][f"listing={'set' if listed else 'null'},card={'set' if carded else 'null'}"] += 1
        diff_paths.update(r["diff"])
        ref_fields.update(f"card:{f}" for f in r["card_linkage"]["reference_fields"])
        offer_vs_owner[f"offer_min_stars={'set' if r['card'].get('offer_min_stars') is not None else 'null'},"
                       f"owner_id={'set' if r['card'].get('owner_id') else 'null'}"] += 1
    return {"compared": len(records), "peer_fields": {k: dict(v) for k, v in fields.items()},
            "diff_paths": dict(diff_paths), "reference_fields": dict(ref_fields),
            "card_peers_returned": sum(r["card_linkage"]["returned"] for r in records),
            "card_peers_unreferenced_by_nft": sum(r["card_linkage"]["returned_unreferenced"] for r in records),
            "offer_vs_owner_on_card": dict(offer_vs_owner)}


async def compare_type(api: Api, gift_id: int) -> list[dict]:
    page = await api(GetResaleStarGiftsRequest(gift_id=gift_id, offset="", limit=PAGE_SIZE))
    raw = to_jsonable(page)
    write_json(OUT / "listings" / f"{gift_id}.json", raw)
    records = []
    for gift in [g for g in raw["gifts"] if g.get("_") == "StarGiftUnique"][:SAMPLE_PER_TYPE]:
        slug = gift["slug"]
        try:
            res = to_jsonable(await api(GetUniqueStarGiftRequest(slug=slug)))
        except RPCError as e:
            record_error(f"seller_compare {slug}", e)
            continue
        write_json(OUT / "cards" / f"{slug}.json", res["gift"])
        write_json(OUT / "peers" / f"{slug}.json", {"users": res["users"], "chats": res["chats"]})
        records.append({"slug": slug, "listing": gift, "card": res["gift"], "diff": sorted(deep_diff(gift, res["gift"])),
                        "listing_linkage": linkage(gift, raw["users"], raw["chats"]),
                        "card_linkage": linkage(res["gift"], res["users"], res["chats"])})
    return records


def stored_controls() -> list[dict]:
    """offer_min_stars vs owner_id on every card already on disk (no new requests)."""
    out = []
    for path in sorted(Path(config.RAW_DIR).glob("unique_*.json")) + sorted((config.RAW_DIR / "unique_sample").glob("*.json")):
        gift = json.loads(path.read_text(encoding="utf-8"))["gift"]
        out.append({"file": path.name, "owner_id_set": bool(gift.get("owner_id")),
                    "offer_min_stars_set": gift.get("offer_min_stars") is not None,
                    "listed": bool(gift.get("resell_amount"))})
    return out


async def main() -> None:
    async with open_api() as api:
        catalog = to_jsonable(await api(GetStarGiftsRequest(hash=0)))
        ids = [g["id"] for g in catalog["gifts"] if g.get("availability_resale")][:GIFT_TYPES]
        records = [r for gift_id in ids for r in await compare_type(api, gift_id)]
    write_json(OUT / "compare_records.json", records)
    summary = {"gift_types": ids, **summarize(records), "stored_cards": stored_controls()}
    write_json(config.PARSED_DIR / "seller_compare.json", summary)
    print({k: v for k, v in summary.items() if k != "stored_cards"})


if __name__ == "__main__":
    run_script(main)
