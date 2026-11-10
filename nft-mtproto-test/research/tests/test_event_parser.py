import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyzers.event_parser import diff_snapshots, normalize_event  # noqa: E402
from analyzers.hypothesis_validator import build_result, verdict  # noqa: E402

OWNED = {"id": 1, "gift_id": 2, "slug": "A-1", "owner_id": {"_": "PeerUser", "user_id": 42}, "owner_name": "N",
         "resell_amount": [{"_": "StarsAmount", "amount": 9}]}
USERS = [{"_": "User", "id": 42, "username": "abc"}]


def test_seller_unknown_without_evidence_even_if_owner_known():
    e = normalize_event("LISTING", OWNED, USERS)
    assert e["owner"]["telegram_id"] == 42 and e["seller"]["telegram_id"] is None
    assert e["seller"]["resolution_status"] == "unknown"


def test_seller_filled_only_on_explicit_evidence():
    assert normalize_event("LISTING", OWNED, USERS, owner_is_seller=True)["seller"]["username"] == "abc"


def test_hidden_owner_has_no_invented_identity():
    e = normalize_event("LISTING", {"slug": "A-2", "owner_name": "@fake"}, [])
    assert e["owner"]["username"] is None and e["owner"]["display_name"] == "@fake"


def test_snapshot_diff_classifies_events():
    events = diff_snapshots({"a": [1], "b": [2], "gone": [3]}, {"a": [1], "b": [5], "new": [1], "back": [1]},
                            frozenset({"back"}))
    assert sorted(events) == [("DELIST_OR_SALE", "gone"), ("LISTING", "new"), ("PRICE_CHANGE", "b"), ("RELIST", "back")]


def test_confirmed_requires_independent_verification():
    assert verdict(100, 100, False)[0] == "partially_confirmed"
    assert verdict(100, 100, True)[0] == "confirmed"


def test_rare_coincidence_is_rejected_not_partial():
    assert verdict(297, 9, False)[0] == "rejected"


def test_no_samples_is_not_tested_and_zero_valid_is_rejected():
    assert verdict(0, 0, False)[0] == "not_tested"
    assert build_result("h", 50, 0, [], [])["status"] == "rejected"
