"""Controls plus listing-vs-card comparison: does getUniqueStarGift reveal more than the listing?"""
from __future__ import annotations

import random

from _common import CONTROL_SLUGS, HYPOTHESES, config, iter_pages, unique_gifts
from analyzers.gift_parser import extract_listing, peer_id
from analyzers.hypothesis_validator import build_result, save_result
from mtproto_client import Api, open_api, record_error, run_script, to_jsonable, write_json
from telethon.errors import RPCError
from telethon.tl.functions.payments import GetResaleStarGiftsRequest, GetUniqueStarGiftRequest
from telethon.tl.types import (StarGiftAttributeBackdrop, StarGiftAttributeIdBackdrop, StarGiftAttributeIdModel,
                               StarGiftAttributeIdPattern, StarGiftAttributeModel, StarGiftAttributePattern)

SAMPLE_SIZE = 25
COMPARED = ("owner_peer_id", "owner_name", "owner_address", "host_peer_id", "gift_address", "prices")


def attribute_filter(gift) -> list:
    """Exact model+pattern+backdrop narrows tens of thousands of listings to a handful."""
    out = []
    for a in gift.attributes:
        if isinstance(a, StarGiftAttributeModel):
            out.append(StarGiftAttributeIdModel(document_id=a.document.id))
        elif isinstance(a, StarGiftAttributePattern):
            out.append(StarGiftAttributeIdPattern(document_id=a.document.id))
        elif isinstance(a, StarGiftAttributeBackdrop):
            out.append(StarGiftAttributeIdBackdrop(backdrop_id=a.backdrop_id))
    return out


async def check_control(api: Api, slug: str) -> dict:
    try:
        res = await api(GetUniqueStarGiftRequest(slug=slug))
    except RPCError as e:
        record_error(f"control {slug}", e)
        return {"slug": slug, "exists": False, "error": type(e).__name__}
    gift = res.gift
    write_json(config.RAW_DIR / f"unique_{slug}.json", to_jsonable(res))
    card = extract_listing(to_jsonable(gift))
    out = {"slug": slug, "exists": True, "listed": bool(gift.resell_amount), "card": card,
           "users": len(res.users), "chats": len(res.chats), "found_in_resale": False, "diffs": {}}
    if gift.resell_amount:
        page = await api(GetResaleStarGiftsRequest(gift_id=gift.gift_id, offset="", limit=100,
                                                   attributes=attribute_filter(gift), attributes_hash=0))
        write_json(config.RAW_DIR / f"resale_filtered_{slug}.json", to_jsonable(page))
        match = next((g for g in page.gifts if g.slug == slug), None)
        out["resale_filtered_count"] = page.count
        if match:
            listing = extract_listing(to_jsonable(match))
            out["found_in_resale"] = True
            out["diffs"] = {f: [listing[f], card[f]] for f in COMPARED if listing[f] != card[f]}
    return out


async def compare_sample(api: Api, listings: dict[str, dict]) -> dict:
    random.seed(0)  # reproducible sample
    sample = random.sample(sorted(listings), min(SAMPLE_SIZE, len(listings)))
    result = {"compared": 0, "identical": 0, "card_has_owner_id_listing_not": 0, "field_diffs": {f: 0 for f in COMPARED}}
    for slug in sample:
        res = await api(GetUniqueStarGiftRequest(slug=slug))
        card, listing = to_jsonable(res.gift), listings[slug]
        write_json(config.RAW_DIR / "unique_sample" / f"{slug}.json", to_jsonable(res))
        flat_c, flat_l = extract_listing(card), extract_listing(listing)
        diffs = [f for f in COMPARED if flat_c[f] != flat_l[f]]
        result["compared"] += 1
        result["identical"] += not diffs
        result["card_has_owner_id_listing_not"] += bool(peer_id(card.get("owner_id")) and not peer_id(listing.get("owner_id")))
        for f in diffs:
            result["field_diffs"][f] += 1
    return result


async def main() -> None:
    listings = unique_gifts(list(iter_pages()))
    async with open_api() as api:
        controls = [await check_control(api, s) for s in CONTROL_SLUGS]
        comparison = await compare_sample(api, listings) if listings else {"compared": 0}
    write_json(config.PARSED_DIR / "unique_compare.json", {"controls": controls, "comparison": comparison})
    print({"controls": [{k: v for k, v in c.items() if k != "card"} for c in controls], "comparison": comparison})
    save_result(HYPOTHESES, build_result("unique_card_reveals_owner_beyond_listing", comparison["compared"],
                                         comparison.get("card_has_owner_id_listing_not", 0), [comparison],
                                         ["Listing and card were fetched at different moments."]))


if __name__ == "__main__":
    run_script(main)
