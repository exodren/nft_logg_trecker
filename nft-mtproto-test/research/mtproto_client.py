"""Read-only Telethon wrapper: allowlist, rate limit, FloodWait, retries, atomic dumps."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

log = logging.getLogger("research")

# Only these requests can leave this process: no send, buy, list or transfer is reachable.
READ_ONLY_REQUESTS = frozenset({
    "GetConfigRequest",
    "GetStarGiftsRequest",
    "GetResaleStarGiftsRequest",
    "GetUniqueStarGiftRequest",
    "GetUniqueStarGiftValueInfoRequest",
    "GetStarGiftActiveAuctionsRequest",
    "GetUsersRequest",
    "ResolveUsernameRequest",
})


class FloodWaitTooLong(RuntimeError):
    def __init__(self, seconds: int) -> None:
        super().__init__(f"FloodWait {seconds}s exceeds limit {config.MAX_FLOOD_WAIT_S}s")
        self.seconds = seconds


class Api:
    """Serial, rate-limited access to Telegram. Collects call statistics."""

    def __init__(self, client: TelegramClient) -> None:
        self.client = client
        self.stats: dict[str, int] = {"ok": 0, "rpc_error": 0, "flood_wait": 0, "network_error": 0}

    async def __call__(self, request: Any) -> Any:
        name = type(request).__name__
        if name not in READ_ONLY_REQUESTS:
            raise PermissionError(f"{name} is not in the read-only allowlist")
        for attempt in range(1, config.MAX_RETRIES + 1):
            await asyncio.sleep(config.REQUEST_DELAY_S)
            try:
                result = await asyncio.wait_for(self.client(request), config.REQUEST_TIMEOUT_S)
            except FloodWaitError as e:
                self.stats["flood_wait"] += 1
                if e.seconds > config.MAX_FLOOD_WAIT_S:
                    raise FloodWaitTooLong(e.seconds) from e
                log.warning("FloodWait %ss on %s", e.seconds, name)
                await asyncio.sleep(e.seconds + 1)
            except RPCError:
                self.stats["rpc_error"] += 1
                raise
            except (OSError, asyncio.TimeoutError, ConnectionError) as e:
                self.stats["network_error"] += 1
                log.warning("%s on %s, attempt %s", type(e).__name__, name, attempt)
                if attempt == config.MAX_RETRIES:
                    raise
                await asyncio.sleep(2**attempt)
            else:
                self.stats["ok"] += 1
                return result
        raise RuntimeError(f"{name}: retries exhausted")


def prepare_session() -> Path:
    """Copy the user's session once; an existing research copy is never replaced."""
    target = config.RESEARCH_SESSION
    if not target.exists():
        if not config.SOURCE_SESSION.exists():
            raise FileNotFoundError("Source session not found. Run ../test_gift.py to log in first.")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config.SOURCE_SESSION, target)
    return target


@asynccontextmanager
async def open_api() -> AsyncIterator[Api]:
    """Connect without any login flow: an unauthorized session fails instead of prompting."""
    api_id, api_hash = config.credentials()
    session = prepare_session().with_suffix("")
    client = TelegramClient(str(session), api_id, api_hash, flood_sleep_threshold=0)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise PermissionError("Session is not authorized. Log in via ../test_gift.py first.")
        yield Api(client)
    finally:
        await client.disconnect()


def to_jsonable(obj: Any) -> Any:
    data = obj.to_dict() if hasattr(obj, "to_dict") else obj
    return json.loads(json.dumps(data, default=str, ensure_ascii=False))


def write_json(path: Path, data: Any) -> Path:
    """Atomic write: a crash never leaves a truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, default=str, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path


def record_error(context: str, exc: BaseException) -> None:
    """Keep every traceback; research errors are data, not noise."""
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"at": datetime.now(timezone.utc).isoformat(), "context": context, "error": type(exc).__name__,
             "traceback": "".join(traceback.format_exception(exc))}
    with open(config.REPORTS_DIR / "errors.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log.error("%s: %s (traceback in errors.jsonl)", context, type(exc).__name__)


def run_script(main: Callable[[], Awaitable[None]]) -> None:
    setup_logging()
    try:
        asyncio.run(main())
    except Exception as e:
        record_error(getattr(main, "__module__", "script"), e)
        sys.exit(1)


def setup_logging() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    logging.getLogger("telethon").setLevel(logging.WARNING)
