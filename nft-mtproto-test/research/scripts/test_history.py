"""Previous owner: what official methods exist (from api.tl) and what value info returns."""
from __future__ import annotations

import re
import urllib.request

from _common import CONTROL_SLUGS, HYPOTHESES, config
from analyzers.hypothesis_validator import build_result, save_result
from mtproto_client import open_api, record_error, run_script, to_jsonable, write_json
from telethon.errors import RPCError
from telethon.tl.functions.payments import GetUniqueStarGiftValueInfoRequest

TL_URL = "https://raw.githubusercontent.com/telegramdesktop/tdesktop/dev/Telegram/SourceFiles/mtproto/scheme/api.tl"


def gift_methods() -> tuple[str | None, list[str]]:
    req = urllib.request.Request(TL_URL, headers={"User-Agent": "tl-schema-check"})
    with urllib.request.urlopen(req, timeout=30) as r:
        tl = r.read().decode("utf-8")
    (config.RAW_DIR / "api.tl").write_text(tl, encoding="utf-8")
    layer = re.search(r"// LAYER (\d+)", tl)
    methods = tl.split("---functions---", 1)[1]
    found = sorted(set(re.findall(r"^(\w+\.\w*[Gg]ift\w*)#", methods, re.M)))
    return layer.group(1) if layer else None, found


async def main() -> None:
    layer, methods = gift_methods()
    history_like = [m for m in methods if re.search("histor|owner|transfer|log", m, re.I)]
    values = {}
    async with open_api() as api:
        for slug in CONTROL_SLUGS:
            try:
                res = to_jsonable(await api(GetUniqueStarGiftValueInfoRequest(slug=slug)))
            except RPCError as e:
                record_error(f"value_info {slug}", e)
                values[slug] = {"error": type(e).__name__}
                continue
            write_json(config.RAW_DIR / f"value_info_{slug}.json", res)
            values[slug] = {k: (type(v).__name__ if isinstance(v, (dict, list)) else v) for k, v in res.items()}
    out = {"tl_layer": layer, "gift_methods": methods, "history_like_methods": history_like, "value_info": values}
    write_json(config.PARSED_DIR / "history.json", out)
    print(out)
    has_owner_field = any("owner" in k or "seller" in k for v in values.values() for k in v)
    save_result(HYPOTHESES, build_result(
        "official_api_exposes_previous_owner", len(CONTROL_SLUGS), int(has_owner_field), [out],
        ["Service messages expose transfers only inside the account's own dialogs.",
         "Two controls only: the absence of a method in api.tl is the main evidence."]))


if __name__ == "__main__":
    run_script(main)
