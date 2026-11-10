"""Resolve Telegram usernames bound to TON addresses via GRAM Explorer (gram.org/api/names)."""
from __future__ import annotations

import asyncio
import sys

import aiohttp

API_URL = "https://gram.org/api/names"
BATCH_SIZE = 25  # API accepts up to 100 per request
TIMEOUT = aiohttp.ClientTimeout(total=10)
TME_SUFFIX = ".t.me"


def _parse(address: str, raw_name: str | None) -> dict:
    if not raw_name or not raw_name.endswith(TME_SUFFIX):
        return {"address": address, "found": False}
    username = raw_name[: -len(TME_SUFFIX)]
    return {
        "address": address,
        "raw_name": raw_name,
        "username": username,
        "telegram_url": f"https://t.me/{username}",
        "found": True,
    }


async def _fetch_batch(session: aiohttp.ClientSession, batch: list[str]) -> list[dict]:
    """One request for a batch. Network/API errors → every address gets found=False + error."""
    try:
        async with session.get(API_URL, params={"a": ",".join(batch)}) as resp:
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        err = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        return [{"address": a, "found": False, "error": err} for a in batch]

    if "error" in data:  # e.g. all addresses invalid
        return [{"address": a, "found": False, "error": data["error"]} for a in batch]

    names, forms = data.get("names", {}), data.get("forms", {})
    # names may be keyed by the input form or by the canonical (forms) form
    return [_parse(a, names.get(a) or names.get(forms.get(a, ""))) for a in batch]


async def resolve_many(addresses: list[str]) -> list[dict]:
    addrs = list(dict.fromkeys(a.strip() for a in addresses if a.strip()))
    batches = [addrs[i : i + BATCH_SIZE] for i in range(0, len(addrs), BATCH_SIZE)]
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        results = await asyncio.gather(*(_fetch_batch(session, b) for b in batches))
    return [r for batch in results for r in batch]


async def resolve_telegram_name(address: str) -> dict | None:
    results = await resolve_many([address])
    return results[0] if results else None


def _print(results: list[dict]) -> None:
    color = sys.stdout.isatty()
    green, red, yellow, dim, reset = (
        ("\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m") if color else ("",) * 5
    )
    for r in results:
        print(f"{dim}{r['address']}{reset}")
        if r["found"]:
            print(f"  {green}✔ @{r['username']}{reset}  {r['telegram_url']}")
        elif "error" in r:
            print(f"  {yellow}⚠ error: {r['error']}{reset}")
        else:
            print(f"  {red}✘ no Telegram binding{reset}")


def main() -> int:
    if len(sys.argv) < 2:
        print(f"usage: python {sys.argv[0]} <ton_address> [more addresses...]", file=sys.stderr)
        return 2
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    results = asyncio.run(resolve_many(sys.argv[1:]))
    _print(results)
    return 1 if any("error" in r for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
