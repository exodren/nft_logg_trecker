"""Offline, from the saved scan. Hypothesis A: owner_id in an active listing is the seller's ID."""
from __future__ import annotations

import asyncio
from collections import Counter

from _common import HYPOTHESES, config, require_scan, unique_gifts
from analyzers.gift_parser import peer_references, peer_type
from analyzers.hypothesis_validator import build_result, save_result
from analyzers.peer_resolver import resolve_telegram_user
from mtproto_client import setup_logging, write_json


def users_explained(pages: list) -> dict:
    """Who are result.users/chats? Count entities referenced by some gift field vs. unexplained ones."""
    by_field: Counter = Counter()
    unexplained = total = 0
    for _, page in pages:
        refs: dict[int, str] = {}
        for g in page["gifts"]:
            refs.update({pid: field for field, pid in peer_references(g).items()})
        for entity in page["users"] + page["chats"]:
            total += 1
            field = refs.get(entity.get("id"))
            by_field[field or "unexplained"] += 1
            unexplained += field is None
    return {"entities_total": total, "referenced_by": dict(by_field), "unexplained": unexplained}


def original_recipient_vs_owner(pages: list) -> dict:
    """Is the recipient in OriginalDetails (returned in result.users) the current owner?"""
    out = {"recipient_in_users": 0, "name_equals_owner_name": 0}
    for _, page in pages:
        users = {u["id"]: u for u in page["users"]}
        for g in page["gifts"]:
            user = users.get(peer_references(g).get("original_recipient_id"))
            if user:
                full = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p)
                out["recipient_in_users"] += 1
                out["name_equals_owner_name"] += full.strip() == (g.get("owner_name") or "").strip()
    return out


async def analyse() -> None:
    pages = require_scan()
    recipient = original_recipient_vs_owner(pages)
    save_result(HYPOTHESES, build_result(
        "original_recipient_is_current_owner", recipient["recipient_in_users"], recipient["name_equals_owner_name"],
        [recipient], ["Display names can change, so a mismatch is not absolute proof for a single gift."]))
    gifts = unique_gifts(pages)
    per_type = Counter(peer_type(g.get("owner_id")) for g in gifts.values())
    statuses: Counter = Counter()
    for _, page in pages:
        for g in page["gifts"]:
            statuses[(await resolve_telegram_user(g.get("owner_id"), page["users"], page["chats"]))["status"]] += 1
    with_user_owner = sum(peer_type(g.get("owner_id")) == "PeerUser" for g in gifts.values())
    summary = {"listings": len(gifts), "owner_id_types": {str(k): v for k, v in per_type.items()},
               "resolution_statuses": dict(statuses), "owner_name_present": sum(bool(g.get("owner_name")) for g in gifts.values()),
               "result_users": users_explained(pages)}
    write_json(config.PARSED_DIR / "owner_seller.json", summary)
    print(summary)
    save_result(HYPOTHESES, build_result(
        "owner_id_is_seller_id", len(gifts), with_user_owner,
        [summary], ["No comparison against the Telegram Marketplace UI was made by this script."]))


if __name__ == "__main__":
    setup_logging()
    asyncio.run(analyse())
