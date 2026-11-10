"""payments.getStarGifts: catalog of base gift types and what can be discovered from it."""
from __future__ import annotations

from collections import Counter

from _common import config
from mtproto_client import Api, open_api, run_script, to_jsonable, write_json
from telethon.tl.functions.payments import GetStarGiftsRequest


async def fetch_gift_types(api: Api) -> list[dict]:
    res = await api(GetStarGiftsRequest(hash=0))
    data = to_jsonable(res)
    write_json(config.RAW_DIR / "star_gifts.json", data)
    return [g for g in data["gifts"] if g.get("_") == "StarGift"]


async def main() -> None:
    async with open_api() as api:
        types = await fetch_gift_types(api)
    summary = {
        "total_types": len(types),
        "limited": sum(bool(g.get("limited")) for g in types),
        "sold_out": sum(bool(g.get("sold_out")) for g in types),
        "upgradable": sum(g.get("upgrade_stars") is not None for g in types),
        "with_resale": sum(bool(g.get("availability_resale")) for g in types),
        "total_resale_listings": sum(g.get("availability_resale") or 0 for g in types),
        "auction": sum(bool(g.get("auction")) for g in types),
        "constructors": Counter(g["_"] for g in types),
        "catalog_fields": sorted({k for g in types for k, v in g.items() if v is not None}),
    }
    write_json(config.PARSED_DIR / "gift_types_summary.json", summary)
    print(summary)


if __name__ == "__main__":
    run_script(main)
