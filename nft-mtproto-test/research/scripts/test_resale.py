"""payments.getResaleStarGifts over an evenly spread sample of gift types.

Every page is saved before the next request, and the scan resumes from those files.
Delete data/raw/resale/ for a fresh scan. Bounds are in config.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from _common import RESALE_DIR, config
from analyzers.gift_parser import extract_listing, field_inventory
from mtproto_client import Api, FloodWaitTooLong, open_api, record_error, run_script, to_jsonable, write_json
from telethon.errors import RPCError
from telethon.tl.functions.payments import GetResaleStarGiftsRequest
from test_star_gifts import fetch_gift_types

CONTROL_TITLES = ("Chill Flame", "Snoop Cigar")


def select_types(types: list[dict], limit: int) -> list[dict]:
    """Controls first, then evenly across popularity, so rare types are sampled as well."""
    listed = sorted((g for g in types if g.get("availability_resale")), key=lambda g: -g["availability_resale"])
    controls = [g for g in listed if g.get("title") in CONTROL_TITLES]
    rest = [g for g in listed if g not in controls]
    slots = max(limit - len(controls), 1)
    step = max(len(rest) / slots, 1)
    return controls + [rest[int(i * step)] for i in range(min(slots, len(rest)))]


def resume_point(gift_id: int) -> tuple[int, str | None]:
    """(next page index, offset); offset None means this gift type is finished."""
    pages = sorted(RESALE_DIR.glob(f"{gift_id}_p*.json"), key=lambda p: int(p.stem.rsplit("_p", 1)[1]))
    if not pages:
        return 0, ""
    last = json.loads(pages[-1].read_text(encoding="utf-8"))
    return len(pages), last.get("next_offset")


async def scan_type(api: Api, gift: dict, stats: dict) -> None:
    gid = gift["id"]
    page, offset = resume_point(gid)
    while offset is not None and page < config.RESALE_MAX_PAGES:
        res = await api(GetResaleStarGiftsRequest(gift_id=gid, offset=offset, limit=config.RESALE_PAGE_SIZE))
        write_json(RESALE_DIR / f"{gid}_p{page}.json", to_jsonable(res))
        stats["pages"] += 1
        stats["listings"] += len(res.gifts)
        offset, page = res.next_offset, page + 1


def write_parsed() -> dict:
    gifts = [g for p in RESALE_DIR.glob("*.json") for g in json.loads(p.read_text(encoding="utf-8"))["gifts"]]
    unique = list({g["slug"]: g for g in gifts}.values())
    write_json(config.PARSED_DIR / "listings.json", [extract_listing(g) for g in unique])
    inventory = field_inventory(unique)
    write_json(config.PARSED_DIR / "field_inventory.json", inventory)
    return {"unique_listings": len(unique), "duplicates": len(gifts) - len(unique)}


async def main() -> None:
    stats = {"types_ok": 0, "types_failed": 0, "pages": 0, "listings": 0}
    async with open_api() as api:
        types = select_types(await fetch_gift_types(api), config.RESALE_MAX_GIFT_TYPES)
        for gift in types:
            try:
                await scan_type(api, gift, stats)
                stats["types_ok"] += 1
            except FloodWaitTooLong as e:
                record_error(f"resale {gift['id']}", e)
                stats["stopped_on_flood"] = e.seconds
                break  # back off entirely; pages already saved, a rerun resumes
            except (RPCError, OSError, TimeoutError) as e:
                record_error(f"resale {gift['id']}", e)
                stats["types_failed"] += 1
        stats["api"] = api.stats
    stats.update(write_parsed())
    write_json(config.PARSED_DIR / "resale_stats.json", stats)
    print(stats)


if __name__ == "__main__":
    run_script(main)
