"""Is a seller link visible at the moment a listing appears? Bounded watch, flags only, no account lookups."""
from __future__ import annotations

import json
import time

from _common import config
from mtproto_client import Api, open_api, record_error, run_script, to_jsonable, write_json
from telethon.errors import RPCError
from telethon.tl.functions.payments import (GetResaleStarGiftsRequest, GetStarGiftsRequest,
                                            GetUniqueStarGiftRequest)

GIFT_TYPES = 10
PAGE_SIZE = 20
WATCH_SECONDS = 600
MAX_CARDS = 60


def flags(gift: dict) -> dict:
    return {f: gift.get(f) is not None for f in ("owner_id", "host_id", "owner_address", "owner_name", "offer_min_stars")}


async def card(api: Api, slug: str) -> dict | None:
    try:
        res = to_jsonable(await api(GetUniqueStarGiftRequest(slug=slug)))
    except RPCError as e:
        record_error(f"event_moment {slug}", e)
        return None
    return {**flags(res["gift"]), "users": len(res["users"])}


async def main() -> None:
    seen: set[str] = set()
    sightings: list[dict] = []
    cycles = 0
    deadline = time.monotonic() + WATCH_SECONDS
    async with open_api() as api:
        catalog = to_jsonable(await api(GetStarGiftsRequest(hash=0)))
        active = sorted((g for g in catalog["gifts"] if g.get("availability_resale")),
                        key=lambda g: g["availability_resale"], reverse=True)
        ids = [g["id"] for g in active[:GIFT_TYPES]]
        while time.monotonic() < deadline:
            for gift_id in ids:
                page = to_jsonable(await api(GetResaleStarGiftsRequest(gift_id=gift_id, offset="", limit=PAGE_SIZE)))
                for position, gift in enumerate(g for g in page["gifts"] if g.get("_") == "StarGiftUnique"):
                    if gift["slug"] in seen:
                        continue
                    seen.add(gift["slug"])
                    if cycles and len(sightings) < MAX_CARDS:  # first cycle is only the baseline
                        sightings.append({"slug": gift["slug"], "position": position, "at": int(time.time()),
                                          "listing": flags(gift), "page_users": len(page["users"]),
                                          "card": await card(api, gift["slug"])})
            cycles += 1
    summary = {"cycles": cycles, "gift_types": len(ids), "baseline_and_seen": len(seen), "new_sightings": len(sightings),
               "owner_id_on_listing": sum(s["listing"]["owner_id"] for s in sightings),
               "owner_id_on_card": sum(bool(s["card"] and s["card"]["owner_id"]) for s in sightings),
               "offer_min_on_card": sum(bool(s["card"] and s["card"]["offer_min_stars"]) for s in sightings)}
    write_json(config.PARSED_DIR / "event_moment.json", {"summary": summary, "sightings": sightings})
    print(json.dumps(summary))


if __name__ == "__main__":
    run_script(main)
