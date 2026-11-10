"""SQLite backup (T9.3) through sqlite3's online backup API: consistent while the app keeps writing."""
from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from app.utils.log import DATABASE

log = logging.getLogger(DATABASE)
KEEP = 7
INTERVAL_S = 24 * 3600


def sqlite_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def backup_sqlite(db: Path, backups_dir: Path, now: datetime, keep: int = KEEP) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    target = backups_dir / f"{db.stem}-{now:%Y%m%d-%H%M%S}.db"
    src, dst = sqlite3.connect(db), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    for old in sorted(backups_dir.glob(f"{db.stem}-*.db"))[:-keep]:
        old.unlink()
    return target


def restore_sqlite(backup: Path, db: Path) -> None:
    """Run with the app stopped."""
    shutil.copy2(backup, db)


async def run_backups(database_url: str, backups_dir: Path, stop: asyncio.Event, clock=datetime.now) -> None:
    db = sqlite_path(database_url)
    if db is None:
        log.info("backups: not SQLite, use the database's own backup tools")
        return
    while not stop.is_set():
        if db.exists():
            try:
                path = await asyncio.to_thread(backup_sqlite, db, backups_dir, clock())
                log.info("database backup: %s", path.name)
            except (sqlite3.Error, OSError) as e:
                log.error("database backup failed: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=INTERVAL_S)
        except asyncio.TimeoutError:
            pass
