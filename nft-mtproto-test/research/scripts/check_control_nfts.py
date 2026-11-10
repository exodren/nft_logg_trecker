"""Which public source, if any, contains the profile from the competitor's log? Exactly three NFTs.

Sources: getUniqueStarGift, getUniqueStarGiftValueInfo, t.me/nft page, TonAPI (anonymous, on-chain gifts only).
Never looks up the usernames themselves and never opens any profile.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from _common import config
from mtproto_client import open_api, record_error, run_script, to_jsonable, write_json
from telethon.errors import RPCError
from telethon.tl.functions.payments import GetUniqueStarGiftRequest, GetUniqueStarGiftValueInfoRequest

TARGETS: dict[str, str | None] = {"TamaGadget-44169": None, "MoonPendant-71710": "tgmefchik",
                                  "ViceCream-47177": "guangying006"}
TONAPI_DELAY_S = 4
OUT = config.RAW_DIR / "source_check"


def fetch(url: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": "research-lab/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        record_error(f"fetch {url}", e)
        return None


def contains(data: Any, target: str | None) -> bool | None:
    """None means there was nothing to compare with or no data from the source."""
    if target is None or data is None:
        return None
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return target.casefold() in text.casefold()


def onchain(address: str, slug: str) -> tuple[dict | None, dict | None]:
    base = f"https://tonapi.io/v2/nfts/{urllib.parse.quote(address)}"
    item, _ = fetch(base), time.sleep(TONAPI_DELAY_S)
    history, _ = fetch(f"{base}/history?limit=100"), time.sleep(TONAPI_DELAY_S)
    parsed = [json.loads(x) if x else None for x in (item, history)]
    write_json(OUT / f"{slug}.tonapi.json", {"item": parsed[0], "history": parsed[1]})
    return parsed[0], parsed[1]


async def check(api: Any, slug: str, target: str | None) -> dict:
    fetched_at = int(time.time())
    try:
        res = to_jsonable(await api(GetUniqueStarGiftRequest(slug=slug)))
    except RPCError as e:
        record_error(f"control {slug}", e)
        return {"slug": slug, "card_error": type(e).__name__}
    gift = res["gift"]
    value = to_jsonable(await api(GetUniqueStarGiftValueInfoRequest(slug=slug)))
    html = await asyncio.to_thread(fetch, f"https://t.me/nft/{slug}")
    write_json(OUT / f"{slug}.mtproto.json", {"fetched_at": fetched_at, "card": res, "value_info": value})
    (OUT / f"{slug}.html").write_text(html or "", encoding="utf-8")
    item, history = (await asyncio.to_thread(onchain, gift["gift_address"], slug)
                     if gift.get("gift_address") else (None, None))
    events = (history or {}).get("events") or []
    stamps = [e["timestamp"] for e in events if e.get("timestamp")]
    return {
        "slug": slug, "listed": bool(gift.get("resell_amount")), "owner_id_set": bool(gift.get("owner_id")),
        "owner_name_set": bool(gift.get("owner_name")), "owner_address_set": bool(gift.get("owner_address")),
        "gift_address_set": bool(gift.get("gift_address")), "card_users": len(res["users"]),
        "value_info_fields": sorted(k for k, v in value.items() if v not in (None, False) and k != "_"),
        "onchain_events": len(events), "onchain_first_last": [min(stamps), max(stamps)] if stamps else None,
        "target_in": {"owner_name": contains(gift.get("owner_name"), target),
                      "owner_address": contains(gift.get("owner_address"), target),
                      "card_users": contains(res["users"], target), "value_info": contains(value, target),
                      "tme_page": contains(html, target), "onchain_item": contains(item, target),
                      "onchain_history": contains(history, target)},
    }


async def main() -> None:
    async with open_api() as api:
        results = [await check(api, slug, target) for slug, target in TARGETS.items()]
    write_json(config.PARSED_DIR / "control_nfts.json", results)
    print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    run_script(main)
