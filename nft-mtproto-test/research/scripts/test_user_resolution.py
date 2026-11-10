"""Can an identity be resolved? Bounded: own account, one named control, few '@x' name candidates."""
from __future__ import annotations

import re

from _common import HYPOTHESES, config, iter_pages, unique_gifts
from analyzers.hypothesis_validator import build_result, save_result
from analyzers.peer_resolver import primary_username
from mtproto_client import Api, open_api, record_error, run_script, write_json
from telethon.errors import RPCError
from telethon.tl.functions.contacts import ResolveUsernameRequest
from telethon.tl.functions.users import GetUsersRequest
from telethon.tl.types import InputUser, InputUserSelf, PeerUser, User

CONTROL_USERNAME = "zetnyk"
MAX_CANDIDATES = 5
AT_NAME = re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,31})")


async def resolve(api: Api, username: str) -> User | None:
    try:
        res = await api(ResolveUsernameRequest(username=username))
    except RPCError as e:
        record_error("resolve_username", e)
        return None
    if not isinstance(res.peer, PeerUser):  # the username may belong to a channel
        return None
    return next((u for u in res.users if isinstance(u, User) and u.id == res.peer.user_id), None)


async def bare_id_lookup(api: Api, user_id: int) -> str:
    """A numeric ID alone, with no access_hash: the case an owner_id without result.users would hit."""
    try:
        got = await api(GetUsersRequest([InputUser(user_id, 0)]))
    except RPCError as e:
        return f"rpc_error:{type(e).__name__}"
    return "empty" if not got or not isinstance(got[0], User) or got[0].min else "resolved"


def name_matches(user: User, owner_name: str) -> bool:
    full = " ".join(p for p in (user.first_name, user.last_name) if p)
    return full.strip() == owner_name.strip()


async def main() -> None:
    gifts = unique_gifts(list(iter_pages()))
    candidates = [(g["owner_name"], m.group(1)) for g in gifts.values()
                  if (m := AT_NAME.search(g.get("owner_name") or ""))]
    out: dict = {"listings": len(gifts), "names_with_at": len(candidates)}
    async with open_api() as api:
        me = (await api(GetUsersRequest([InputUserSelf()])))[0]
        out["self_resolves"] = isinstance(me, User)
        control = await resolve(api, CONTROL_USERNAME)
        out["control_exists"] = control is not None
        if control:
            out["control_id_found"] = True
            names = {control.username or ""} | {u.username for u in control.usernames or []}
            out["control_username_matches"] = CONTROL_USERNAME.casefold() in {n.casefold() for n in names}
            out["control_bare_id_lookup"] = await bare_id_lookup(api, control.id)
            write_json(config.PARSED_DIR / "control_user.json",
                       {"id": control.id, "username": primary_username(control.to_dict())})
        checks = {"tested": 0, "exists": 0, "display_name_matches_owner_name": 0}
        for owner_name, username in candidates[:MAX_CANDIDATES]:
            user = await resolve(api, username)
            checks["tested"] += 1
            checks["exists"] += user is not None
            checks["display_name_matches_owner_name"] += bool(user and name_matches(user, owner_name))
        out["at_name_candidates"] = checks
    write_json(config.PARSED_DIR / "user_resolution.json", out)
    print(out)
    save_result(HYPOTHESES, build_result(
        "at_in_owner_name_is_owners_username", checks["tested"], checks["display_name_matches_owner_name"], [checks],
        ["Only names containing @ are covered.", "A match shows the same display name, which is strong but not proof."]))


if __name__ == "__main__":
    run_script(main)
