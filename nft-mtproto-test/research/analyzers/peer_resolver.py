"""Peer -> Telegram identity. Never invents a username; an unknown stays None."""
from __future__ import annotations

from typing import Any

UNRESOLVED_FIELDS = {"username": None, "first_name": None, "last_name": None}


def _as_dict(obj: Any) -> dict | None:
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


def primary_username(user: dict) -> str | None:
    """Plain username, else the active one from `usernames` (collectible-username accounts)."""
    if user.get("username"):
        return user["username"]
    active = [u for u in user.get("usernames") or [] if u.get("active")]
    return active[0]["username"] if active else None


def _result(telegram_id: int | None, status: str, user: dict | None = None) -> dict:
    fields = dict(UNRESOLVED_FIELDS)
    if user:
        fields = {"username": primary_username(user), "first_name": user.get("first_name"),
                  "last_name": user.get("last_name")}
    return {"telegram_id": telegram_id, **fields, "resolved": bool(user), "status": status,
            "username_public": bool(fields["username"]) if user else None}


async def resolve_telegram_user(peer: Any, users: list, chats: list, api: Any = None) -> dict:
    """Check the peer type, look in the response, then (with `api`) try the session entity cache.

    status: no_peer | not_a_user | in_response | via_session_cache | id_only_no_access_hash | rpc_error
    """
    peer_d = _as_dict(peer)
    if not peer_d:
        return _result(None, "no_peer")
    if peer_d.get("_") != "PeerUser":
        return _result(None, "not_a_user")
    uid = peer_d["user_id"]
    found = next((u for u in map(_as_dict, users) if u.get("_") == "User" and u.get("id") == uid), None)
    if found:
        return _result(uid, "in_response", found)
    if api is None:
        return _result(uid, "id_only_no_access_hash")
    return await _resolve_via_mtproto(uid, api)


async def _resolve_via_mtproto(uid: int, api: Any) -> dict:
    from telethon.errors import RPCError
    from telethon.tl.functions.users import GetUsersRequest
    from telethon.tl.types import InputUser, PeerUser

    try:
        cached = await api.client.get_input_entity(PeerUser(uid))
    except ValueError:  # Telegram needs an access_hash; a bare ID is not addressable
        return _result(uid, "id_only_no_access_hash")
    try:
        got = await api(GetUsersRequest([InputUser(uid, cached.access_hash)]))
    except RPCError:
        return _result(uid, "rpc_error")
    user = _as_dict(got[0]) if got else None
    if not user or user.get("_") != "User":
        return _result(uid, "id_only_no_access_hash")
    return _result(uid, "via_session_cache", user)
