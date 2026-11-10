"""Offsets, sort orders, empty/invalid input, and whether new listings surface on page one."""
from __future__ import annotations

import asyncio

from _common import config
from mtproto_client import Api, open_api, record_error, run_script, write_json
from telethon.errors import RPCError
from telethon.tl.functions.payments import GetResaleStarGiftsRequest
from test_star_gifts import fetch_gift_types

PAGE = 50
EXHAUST_MAX_PAGES = 6
REPOLL_DELAY_S = 90


async def page(api: Api, gift_id: int, offset: str = "", limit: int = PAGE, **flags):
    return await api(GetResaleStarGiftsRequest(gift_id=gift_id, offset=offset, limit=limit, **flags))


async def exhaust(api: Api, gift_id: int) -> dict:
    slugs, offset, pages = [], "", 0
    while pages < EXHAUST_MAX_PAGES:
        r = await page(api, gift_id, offset)
        slugs += [g.slug for g in r.gifts]
        pages += 1
        if not r.next_offset:
            return {"count": r.count, "pages": pages, "fetched": len(slugs), "unique": len(set(slugs)), "exhausted": True}
        offset = r.next_offset
    return {"count": r.count, "pages": pages, "fetched": len(slugs), "unique": len(set(slugs)), "exhausted": False}


async def sort_orders(api: Api, gift_id: int) -> dict:
    """Which ordering does each flag give? Default matters most: is it newest-first?"""
    out = {}
    for name, flags in {"default": {}, "by_price": {"sort_by_price": True}, "by_num": {"sort_by_num": True}}.items():
        r = await page(api, gift_id, limit=20, **flags)
        out[name] = {"nums": [g.num for g in r.gifts][:20], "first_prices": [g.resell_amount[0].amount for g in r.gifts][:5]}
    return out


async def limits_and_edges(api: Api, gift_id: int, empty_id: int | None) -> dict:
    out = {}
    for label, kwargs in {"limit_100": {"limit": 100}, "limit_101": {"limit": 101}, "bad_offset": {"offset": "garbage"},
                          "unknown_gift": {"gift_id": 1}, "no_listings": {"gift_id": empty_id}}.items():
        if kwargs.get("gift_id", gift_id) is None:
            out[label] = "no such gift type in catalog"
            continue
        try:
            r = await page(api, kwargs.pop("gift_id", gift_id), **kwargs)
            out[label] = {"count": r.count, "got": len(r.gifts), "next_offset": bool(r.next_offset)}
        except RPCError as e:
            record_error(f"pagination edge {label}", e)
            out[label] = f"rpc_error:{type(e).__name__}:{e.message}"
    return out


async def freshness(api: Api, gift_id: int) -> dict:
    a = await page(api, gift_id)
    await asyncio.sleep(REPOLL_DELAY_S)
    b = await page(api, gift_id)
    before = [g.slug for g in a.gifts]
    after = [g.slug for g in b.gifts]
    new = [s for s in after if s not in before]
    return {"count_t0": a.count, "count_t1": b.count, "delay_s": REPOLL_DELAY_S, "new_on_page": len(new),
            "new_positions": [after.index(s) for s in new], "left_page": sum(s not in after for s in before)}


async def main() -> None:
    async with open_api() as api:
        types = await fetch_gift_types(api)
        listed = sorted((g for g in types if g.get("availability_resale")), key=lambda g: g["availability_resale"])
        empty = next((g["id"] for g in types if not g.get("availability_resale")), None)
        big = listed[-1]["id"]
        small = next((g["id"] for g in listed if g["availability_resale"] >= 2), listed[0]["id"])
        out = {
            "exhaust_small_type": await exhaust(api, small),
            "exhaust_big_type_bounded": await exhaust(api, big),
            "sort_orders": await sort_orders(api, big),
            "limits_and_edges": await limits_and_edges(api, big, empty),
            "freshness": await freshness(api, big),
            "api": api.stats,
        }
    write_json(config.PARSED_DIR / "pagination.json", out)
    print(out)


if __name__ == "__main__":
    run_script(main)
