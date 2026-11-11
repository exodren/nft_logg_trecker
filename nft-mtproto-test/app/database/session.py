"""Async engine, sessions and schema migration at startup."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import ROOT_DIR
from app.utils.log import DATABASE

log = logging.getLogger(DATABASE)
ALEMBIC_INI = ROOT_DIR / "alembic.ini"


def create_engine(database_url: str) -> AsyncEngine:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(database_url)
    if url.get_backend_name() == "sqlite":
        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")  # bot reads while the scanner writes
            cur.execute("PRAGMA synchronous=NORMAL")  # safe with WAL (no corruption), shorter commits
            cur.execute("PRAGMA busy_timeout=30000")  # other processes (scripts, backup) wait instead of failing
            cur.close()
    return engine


_writing: ContextVar[bool] = ContextVar("db_writing", default=False)


class Sessions:
    """async_sessionmaker whose begin() takes one process-wide write lock.

    SQLite allows one writer; its busy handler polls and is unfair, so under load a writer could time out with
    'database is locked'. Here in-process writers queue FIFO on an asyncio.Lock; readers (sessions()) use WAL
    snapshots and never wait. A begin() nested inside another begin() in the same task would deadlock, so it raises.
    """

    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker
        self._write = asyncio.Lock()

    def __call__(self, **kw: object) -> AsyncSession:
        return self._maker(**kw)

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[AsyncSession]:
        if _writing.get():
            raise RuntimeError("nested write transaction: sessions.begin() inside sessions.begin()")
        token = _writing.set(True)
        try:
            async with self._write, self._maker.begin() as s:
                yield s
        finally:
            _writing.reset(token)


def session_factory(engine: AsyncEngine) -> Sessions:
    return Sessions(async_sessionmaker(engine, expire_on_commit=False))


def _upgrade(database_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


async def migrate(database_url: str) -> None:
    """Alembic's env.py runs its own event loop, so it goes to a worker thread."""
    await asyncio.to_thread(_upgrade, database_url)
    log.info("database schema is up to date")
