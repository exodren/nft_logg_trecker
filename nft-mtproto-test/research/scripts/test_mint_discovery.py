"""Mint discovery: is availability_issued the newest number, and do slugs just past it exist?"""
from __future__ import annotations

import asyncio
import json

from _common import config
from mtproto_client import Api, open_api, record_error, run_script, write_json
from telethon.errors import RPCError
from telethon.tl.functions.payments import GetResaleStarGiftsRequest, GetUniqueStarGiftRequest
from test_star_gifts import fetch_gift_types

TYPES_TO_TEST = 3
PROBE_OFFSETS = (-1, 0, 1, 2)
POLL_INTERVAL_S = 60


async def issued(api: Api, gift_id: int) -> tuple[int, str] | None:
    res = await api(GetResaleStarGiftsRequest(gift_id=gift_id, offset="", limit=1))
    return (res.gifts[0].availability_issued, res.gifts[0].title) if res.gifts else None


async def slug_exists(api: Api, slug: str) -> bool | str:
    try:
        await api(GetUniqueStarGiftRequest(slug=slug))
        return True
    except RPCError as e:
        if "INVALID" in str(e).upper() or "NOT_FOUND" in str(e).upper():
            return False
        record_error(f"mint probe {slug}", e)
        return type(e).__name__


async def main() -> None:
    results = []
    async with open_api() as api:
        types = sorted((g for g in await fetch_gift_types(api) if g.get("availability_resale")),
                       key=lambda g: -g["availability_resale"])[:TYPES_TO_TEST]
        for g in types:
            first = await issued(api, g["id"])
            if first is None:
                continue
            count, title = first
            prefix = title.replace(" ", "").replace("-", "")
            probes = {str(d): await slug_exists(api, f"{prefix}-{count + d}") for d in PROBE_OFFSETS}
            results.append({"gift_id": g["id"], "title": title, "issued_t0": count, "catalog_remains": g.get("availability_remains"), "probes": probes})
        await asyncio.sleep(POLL_INTERVAL_S)
        for r in results:
            later = await issued(api, r["gift_id"])
            r["issued_after_interval"] = later[0] if later else None
    write_json(config.PARSED_DIR / "mint_discovery.json", {"poll_interval_s": POLL_INTERVAL_S, "results": results})
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    run_script(main)
