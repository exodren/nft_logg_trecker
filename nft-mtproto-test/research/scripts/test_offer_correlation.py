"""offer_min_stars vs owner_id on random cards (listed or not): the positive control listings cannot give.

Bounded to PER_TYPE cards of each type. Stores flags only: no owner IDs, names or raw responses.
"""
from __future__ import annotations

import json
import random
from collections import Counter

from _common import config
from mtproto_client import open_api, record_error, run_script, write_json
from telethon.errors import RPCError
from telethon.tl.functions.payments import GetUniqueStarGiftRequest

PER_TYPE = 8


async def main() -> None:
    records = json.loads((config.RAW_DIR / "seller_discovery" / "compare_records.json").read_text(encoding="utf-8"))
    types = {r["slug"].rsplit("-", 1)[0]: r["card"]["availability_issued"] for r in records}
    random.seed(0)
    slugs = [f"{name}-{random.randint(1, issued)}" for name, issued in sorted(types.items()) for _ in range(PER_TYPE)]
    combos: Counter = Counter()
    missing = 0
    async with open_api() as api:
        for slug in slugs:
            try:
                gift = (await api(GetUniqueStarGiftRequest(slug=slug))).gift
            except RPCError as e:
                record_error(f"offer_correlation {slug}", e)
                missing += 1
                continue
            combos[f"owner_id={'set' if gift.owner_id else 'null'},"
                   f"offer_min_stars={'set' if gift.offer_min_stars is not None else 'null'},"
                   f"listed={bool(gift.resell_amount)}"] += 1
    out = {"requested": len(slugs), "not_found": missing, "types": sorted(types), "combinations": dict(combos)}
    write_json(config.PARSED_DIR / "offer_correlation.json", out)
    print(out)


if __name__ == "__main__":
    run_script(main)
