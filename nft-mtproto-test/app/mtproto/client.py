"""Read-only Telethon wrapper for the app. Same safety rules as research/mtproto_client.py:
allowlist of read methods, request pacing, bounded FloodWait, bounded retries, never an interactive login.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError

from app.config import ROOT_DIR, Settings
from app.utils.log import MTPROTO

log = logging.getLogger(MTPROTO)

READ_ONLY_REQUESTS = frozenset({"GetStarGiftsRequest", "GetResaleStarGiftsRequest", "GetUniqueStarGiftRequest"})
SOURCE_SESSION = ROOT_DIR / "sessions" / "mtproto_test.session"  # created by test_gift.py
REQUEST_DELAY_S = 1.5
REQUEST_TIMEOUT_S = 30.0
MAX_FLOOD_WAIT_S = 120
MAX_RETRIES = 3
MAX_RECONNECTS = 3

Sleep = Callable[[float], Awaitable[None]]


class MTProtoError(RuntimeError):
    pass


class NotAuthorized(MTProtoError):
    pass


class FloodWaitTooLong(MTProtoError):
    def __init__(self, seconds: int) -> None:
        super().__init__(f"FloodWait {seconds}s exceeds {MAX_FLOOD_WAIT_S}s")
        self.seconds = seconds


def prepare_session(target: Path, source: Path = SOURCE_SESSION) -> Path:
    """The app works on its own copy: one Telethon SQLite session cannot be shared by two processes."""
    if not target.exists():
        if not source.exists():
            raise NotAuthorized(f"нет сессии {target.name}; войдите один раз: python test_gift.py")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return target


class MTProtoClient:
    def __init__(self, settings: Settings, client_factory: Callable[..., Any] = TelegramClient,
                 sleep: Sleep = asyncio.sleep, delay_s: float = REQUEST_DELAY_S) -> None:
        settings.require("mtproto")
        self._settings = settings
        self._factory = client_factory
        self._sleep = sleep
        self._delay = delay_s
        self._client: Any = None
        self.stats = {"ok": 0, "flood_wait": 0, "network_error": 0, "rpc_error": 0, "reconnects": 0}

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected()

    async def connect(self) -> None:
        session = prepare_session(self._settings.telegram_session)
        self._client = self._factory(str(session.with_suffix("")), self._settings.api_id, self._settings.api_hash,
                                     flood_sleep_threshold=0)
        await self._client.connect()
        if not await self._client.is_user_authorized():
            await self.close()
            raise NotAuthorized("сессия не авторизована; войдите: python test_gift.py")
        log.info("MTProto connected")

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None
            log.info("MTProto disconnected")

    async def _reconnect(self) -> None:
        for attempt in range(1, MAX_RECONNECTS + 1):
            self.stats["reconnects"] += 1
            try:
                if self._client is not None:
                    await self._client.disconnect()
                await self._client.connect()
                return
            except (OSError, ConnectionError, asyncio.TimeoutError) as e:
                log.warning("reconnect %s/%s failed: %s", attempt, MAX_RECONNECTS, type(e).__name__)
                await self._sleep(2**attempt)
        raise MTProtoError("reconnect attempts exhausted")

    async def __call__(self, request: Any) -> Any:
        name = type(request).__name__
        if name not in READ_ONLY_REQUESTS:
            raise PermissionError(f"{name} is not in the read-only allowlist")
        if self._client is None:
            raise MTProtoError("not connected")
        for attempt in range(1, MAX_RETRIES + 1):
            await self._sleep(self._delay)
            try:
                result = await asyncio.wait_for(self._client(request), REQUEST_TIMEOUT_S)
            except FloodWaitError as e:
                self.stats["flood_wait"] += 1
                if e.seconds > MAX_FLOOD_WAIT_S:
                    raise FloodWaitTooLong(e.seconds) from e
                log.warning("FloodWait %ss on %s", e.seconds, name)
                await self._sleep(e.seconds + 1)
            except RPCError:
                self.stats["rpc_error"] += 1
                raise
            except (OSError, ConnectionError, asyncio.TimeoutError) as e:
                self.stats["network_error"] += 1
                log.warning("%s on %s, attempt %s/%s", type(e).__name__, name, attempt, MAX_RETRIES)
                if attempt == MAX_RETRIES:
                    raise MTProtoError(f"{name}: network retries exhausted") from e
                await self._reconnect()
            else:
                self.stats["ok"] += 1
                return result
        raise MTProtoError(f"{name}: retries exhausted")
