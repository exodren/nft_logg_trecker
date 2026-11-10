"""Telegram Log Sender: service logs go to exactly one Telegram group, LOG_GROUP_ID.

Isolation rules:
- the recipient is fixed at construction from LOG_GROUP_ID and validated once (numeric id, getChat type
  group/supergroup); there is no parameter, setting, command or message that can change it, and no fallback;
- every send goes through `_guard`, the single place that checks the recipient;
- the sender's own failures, aiogram's logs and anything logged while sending are written to the local
  log file only, so a failing send can never trigger another Telegram send;
- lines about hidden owners are never forwarded (owner privacy rule).
User NFT notifications are a separate mechanism (app/services/notifications.py) and never come here.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import time
from typing import Any, Awaitable, Callable

from app.providers.base import HIDDEN_OWNER_TEXT
from app.utils.log import MaskingFormatter

# Not forwarded to Telegram by TelegramLogHandler: this logger writes only to app.log.
LOCAL = "app.logsender"
local_log = logging.getLogger(LOCAL)

ALLOWED_CHAT_TYPES = frozenset({"group", "supergroup"})
CHAT_ID_RE = re.compile(r"^-\d{1,20}$")  # groups and supergroups have negative ids
MIN_LEVEL = logging.WARNING  # no DEBUG/INFO flood into the group
MAX_TEXT = 3500  # Telegram limit is 4096
QUEUE_SIZE = 500
DEDUP_WINDOW_S = 60.0
IGNORED_PREFIXES = (LOCAL, "aiogram")

_sending = contextvars.ContextVar("log_sender_sending", default=False)

Send = Callable[[int, str], Awaitable[Any]]
GetChat = Callable[[int], Awaitable[Any]]


class RecipientNotAllowed(PermissionError):
    pass


class TelegramLogSender:
    def __init__(self, raw_group_id: str | None, send: Send, get_chat: GetChat) -> None:
        self._raw = (raw_group_id or "").strip()
        self._send = send
        self._get_chat = get_chat
        self._allowed: int | None = None  # set only by validate()
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._loop: asyncio.AbstractEventLoop | None = None
        self.dropped = 0

    @property
    def allowed_chat_id(self) -> int | None:
        return self._allowed

    async def validate(self) -> bool:
        """LOG_GROUP_ID must exist, be numeric and point to a group or supergroup. No other group is tried."""
        if not self._raw:
            local_log.error("LOG_GROUP_ID is not set: Telegram log sending disabled")
            return False
        if not CHAT_ID_RE.match(self._raw):
            local_log.error("LOG_GROUP_ID is not a valid group chat id: Telegram log sending disabled")
            return False
        chat_id = int(self._raw)
        token = _sending.set(True)
        try:
            chat = await self._get_chat(chat_id)
        except Exception as e:
            local_log.error("LOG_GROUP_ID check failed (%s): Telegram log sending disabled", type(e).__name__)
            return False
        finally:
            _sending.reset(token)
        chat_type = str(getattr(chat, "type", "") or "")
        if getattr(chat, "id", chat_id) != chat_id or chat_type not in ALLOWED_CHAT_TYPES:
            local_log.error("LOG_GROUP_ID points to a %r chat, only group/supergroup allowed: Telegram log "
                            "sending disabled", chat_type or "unknown")
            return False
        self._allowed = chat_id
        return True

    def _guard(self, chat_id: int) -> int:
        """The only recipient check. Anything but the validated LOG_GROUP_ID is refused."""
        if self._allowed is None or chat_id != self._allowed:
            raise RecipientNotAllowed("service logs may only go to LOG_GROUP_ID")
        return chat_id

    async def _deliver(self, chat_id: int, text: str) -> bool:
        try:
            target = self._guard(chat_id)
        except RecipientNotAllowed:
            local_log.error("blocked a service log addressed to a chat other than LOG_GROUP_ID")
            return False
        token = _sending.set(True)
        try:
            await self._send(target, text[:MAX_TEXT])
            return True
        except Exception as e:  # local file only; never re-enters the Telegram handler
            local_log.error("sending a log to LOG_GROUP_ID failed: %s", type(e).__name__)
            return False
        finally:
            _sending.reset(token)

    async def send_log(self, text: str) -> bool:
        """The public entry point. It takes no recipient: the recipient is always LOG_GROUP_ID."""
        if self._allowed is None:
            return False
        return await self._deliver(self._allowed, text)

    def enqueue(self, text: str) -> None:
        """Called by the logging handler, possibly from another thread. Never blocks, never raises."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def put() -> None:
            try:
                self._queue.put_nowait(text)
            except asyncio.QueueFull:
                self.dropped += 1
        try:
            loop.call_soon_threadsafe(put)
        except RuntimeError:
            pass

    async def run(self, stop: asyncio.Event) -> None:
        if not await self.validate():
            return  # disabled; local file logging continues untouched
        self._loop = asyncio.get_running_loop()
        local_log.info("Telegram log sending enabled")
        while not stop.is_set():
            getter = asyncio.create_task(self._queue.get())
            stopper = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait({getter, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if getter not in done:
                getter.cancel()
                break
            stopper.cancel()
            await self.send_log(getter.result())
        self._loop = None


class TelegramLogHandler(logging.Handler):
    """Logging bridge: WARNING and above, masked, deduplicated, never its own records."""

    def __init__(self, sender: TelegramLogSender, secrets: list[str] | tuple[str, ...] = ()) -> None:
        super().__init__(level=MIN_LEVEL)
        self._sender = sender
        self.setFormatter(MaskingFormatter(secrets))
        self._recent: dict[str, float] = {}

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if _sending.get() or record.name.startswith(IGNORED_PREFIXES):
                return
            text = self.format(record)
            if HIDDEN_OWNER_TEXT in text:
                return
            now = time.monotonic()
            key = f"{record.name}:{record.getMessage()}"
            if now - self._recent.get(key, -DEDUP_WINDOW_S) < DEDUP_WINDOW_S:
                return
            if len(self._recent) > 1000:
                self._recent.clear()
            self._recent[key] = now
            self._sender.enqueue(text)
        except Exception:  # a logging handler must never raise into the caller
            self.handleError(record)
