"""Console + rotating file logging with per-component categories and secret masking."""
from __future__ import annotations

import logging
import re
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Iterable

MTPROTO = "app.mtproto"
API = "app.api"
DATABASE = "app.database"
BOT = "app.bot"
CATEGORIES = (MTPROTO, API, DATABASE, BOT)

MASK = "***"
# Bot tokens (123:ABC...) and Kartoshka tokens (id:secret) even when they are not in the settings.
TOKEN_RE = re.compile(r"\b\d{1,12}:[A-Za-z0-9_-]{16,}\b")
# ?token=... in logged URLs.
QUERY_TOKEN_RE = re.compile(r"(token=)[^&\s]+", re.IGNORECASE)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# logs/app.log is a plain text file, rolled over every 6 hours (UTC) into app.log.YYYY-MM-DD_HH.
ROTATE_HOURS = 6
BACKUPS = 28  # 7 days


def mask(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, MASK)
    text = QUERY_TOKEN_RE.sub(rf"\g<1>{MASK}", text)
    return TOKEN_RE.sub(MASK, text)


class MaskingFormatter(logging.Formatter):
    """Masks the fully formatted record, tracebacks included."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__(LOG_FORMAT)
        self.secrets = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        return mask(super().format(record), self.secrets)


def setup_logging(level: str = "INFO", log_dir: Path | None = None, secrets: Iterable[str] = ()) -> None:
    formatter = MaskingFormatter(secrets)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file = TimedRotatingFileHandler(log_dir / "app.log", when="H", interval=ROTATE_HOURS, backupCount=BACKUPS,
                                        encoding="utf-8", utc=True)
        file.setFormatter(formatter)
        root.addHandler(file)
    root.setLevel(level)
    for noisy in ("telethon", "aiogram.event", "aiosqlite", "sqlalchemy.engine", "alembic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
