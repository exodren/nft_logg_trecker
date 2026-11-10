"""First of 20 saved resale lots for which Telegram itself returns owner_id. One read-only card request per lot.

owner_name is never used to look anyone up; lots without owner_id are skipped with no further requests.
"""
from __future__ import annotations

import json
from typing import Any

from _common import RESALE_DIR, config
from mtproto_client import open_api, record_error, run_script, to_jsonable, write_json
from telethon.errors import RPCError
from telethon.tl.functions.payments import GetUniqueStarGiftRequest

PAGE = RESALE_DIR / "5843762284240831056_p0.json"
FIRST_LOT, LOT_COUNT = 1, 20  # lot 0 was IonGem-3185, already reviewed
OUT = config.PARSED_DIR / "resale_public_owner.json"


def owner_user(card: dict[str, Any]) -> dict[str, Any] | None:
    """The owner's entry from this same response, if Telegram included it."""
    owner_id = (card["gift"].get("owner_id") or {}).get("user_id")
    return next((u for u in card.get("users", []) if owner_id is not None and u.get("id") == owner_id), None)


async def main() -> None:
    page = json.loads(PAGE.read_text(encoding="utf-8"))
    slugs = [g["slug"] for g in page["gifts"] if g.get("_") == "StarGiftUnique"][FIRST_LOT:FIRST_LOT + LOT_COUNT]
    rows: list[dict[str, Any]] = []
    match = None
    async with open_api() as api:
        for slug in slugs:
            try:
                card = to_jsonable(await api(GetUniqueStarGiftRequest(slug=slug)))
            except RPCError as e:
                record_error(f"resale_public_owner {slug}", e)
                rows.append({"slug": slug, "error": type(e).__name__})
                continue
            has_owner = card["gift"].get("owner_id") is not None
            rows.append({"slug": slug, "owner_id_set": has_owner})
            print(slug, "owner_id" if has_owner else "null")
            if has_owner:
                user = owner_user(card)
                match = {"slug": slug, "gift": card["gift"], "owner_user_in_response": user is not None,
                         "owner_username": (user or {}).get("username")}
                break
    write_json(OUT, {"checked": len(rows), "rows": rows, "match": match})
    print("RESULT", json.dumps({"checked": len(rows), "match_slug": match and match["slug"]}, ensure_ascii=False))


if __name__ == "__main__":
    run_script(main)
