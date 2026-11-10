"""Offline. Hypothesis B (host_id) and C (owner_address), with case counts from the scan."""
from __future__ import annotations

from _common import HYPOTHESES, config, require_scan, unique_gifts
from analyzers.gift_parser import peer_id
from analyzers.hypothesis_validator import build_result, save_result
from mtproto_client import setup_logging, write_json


def main() -> None:
    gifts = list(unique_gifts(require_scan()).values())
    has = lambda g, f: g.get(f) is not None  # noqa: E731
    cases = {
        "total": len(gifts),
        "owner_id": sum(has(g, "owner_id") for g in gifts),
        "host_id": sum(has(g, "host_id") for g in gifts),
        "owner_id_and_host_id": sum(has(g, "owner_id") and has(g, "host_id") for g in gifts),
        "host_differs_from_owner": sum(has(g, "owner_id") and has(g, "host_id")
                                       and peer_id(g["owner_id"]) != peer_id(g["host_id"]) for g in gifts),
        "owner_address": sum(has(g, "owner_address") for g in gifts),
        "gift_address_onchain": sum(has(g, "gift_address") for g in gifts),
        "only_owner_name": sum(has(g, "owner_name") and not has(g, "owner_id") and not has(g, "owner_address")
                               for g in gifts),
        "no_owner_data": sum(not (has(g, "owner_name") or has(g, "owner_id") or has(g, "owner_address")) for g in gifts),
        "owner_address_equals_gift_address": sum(has(g, "owner_address") and g["owner_address"] == g.get("gift_address")
                                                 for g in gifts),
    }
    write_json(config.PARSED_DIR / "owner_host_cases.json", cases)
    print(cases)
    save_result(HYPOTHESES, build_result("host_id_is_display_profile_not_owner", cases["host_id"],
                                         cases["host_differs_from_owner"], [cases],
                                         ["Semantics of host_id are only observable when both peers are present."]))
    save_result(HYPOTHESES, build_result("owner_address_is_ton_wallet_separate_from_account", cases["owner_address"],
                                         cases["owner_address"] - cases["owner_address_equals_gift_address"], [cases],
                                         ["Wallet to Telegram-account binding is not derivable from this field."]))


if __name__ == "__main__":
    setup_logging()
    main()
