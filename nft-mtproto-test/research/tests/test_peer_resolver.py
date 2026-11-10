import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyzers.peer_resolver import resolve_telegram_user  # noqa: E402

PEER = {"_": "PeerUser", "user_id": 42}


def run(*args):
    return asyncio.run(resolve_telegram_user(*args))


def test_absent_peer():
    assert run(None, [], [])["status"] == "no_peer"


def test_channel_is_not_a_user():
    r = run({"_": "PeerChannel", "channel_id": 1}, [], [])
    assert r["status"] == "not_a_user" and r["telegram_id"] is None


def test_found_in_response():
    r = run(PEER, [{"_": "User", "id": 42, "username": "abc", "first_name": "A"}], [])
    assert (r["telegram_id"], r["username"], r["resolved"], r["username_public"]) == (42, "abc", True, True)


def test_keeps_id_when_user_has_no_username():
    r = run(PEER, [{"_": "User", "id": 42, "first_name": "A"}], [])
    assert r["telegram_id"] == 42 and r["username"] is None and r["username_public"] is False


def test_active_collectible_username_used():
    u = {"_": "User", "id": 42, "usernames": [{"username": "off", "active": False}, {"username": "on", "active": True}]}
    assert run(PEER, [u], [])["username"] == "on"


def test_unknown_user_keeps_id_and_invents_nothing():
    r = run(PEER, [{"_": "User", "id": 1, "username": "other"}], [])
    assert (r["telegram_id"], r["username"], r["resolved"]) == (42, None, False)
