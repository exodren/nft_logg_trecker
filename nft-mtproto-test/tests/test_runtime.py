import asyncio
import sqlite3
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app import main as app_main
from app import supervisor as sup
from app.database.models import Listing, ProcessingTask
from app.providers.base import OwnerStatus
from app.providers.mock import MockProvider
from app.services.backup import backup_sqlite, restore_sqlite, sqlite_path
from app.supervisor import FatalComponentError, Supervisor


async def test_restart_one_component_keeps_others_running():
    runs = {"a": 0, "b": 0}

    def component(name):
        async def run(stop):
            runs[name] += 1
            await stop.wait()
        return run
    s = Supervisor()
    s.add("a", component("a"))
    s.add("b", component("b"))
    s.start_all()
    await asyncio.sleep(0)
    await s.restart("a")
    await asyncio.sleep(0)
    assert runs == {"a": 2, "b": 1} and s.state() == {"a": True, "b": True}
    await s.stop_all()
    assert s.state() == {"a": False, "b": False}


async def test_crashed_component_is_restarted_fatal_is_not(monkeypatch):
    monkeypatch.setattr(sup, "CRASH_BACKOFF_S", (0,))
    calls = {"crash": 0, "fatal": 0}

    async def crashing(stop):
        calls["crash"] += 1
        if calls["crash"] < 3:
            raise RuntimeError("boom")
        await stop.wait()

    async def fatal(stop):
        calls["fatal"] += 1
        raise FatalComponentError("no session")
    s = Supervisor()
    s.add("crash", crashing)
    s.add("fatal", fatal)
    s.start_all()
    for _ in range(20):
        await asyncio.sleep(0.01)
    assert calls == {"crash": 3, "fatal": 1}
    assert s.state() == {"crash": True, "fatal": False}
    await s.stop_all()


def test_backup_restore_and_rotation(tmp_path):
    db = tmp_path / "tracker.db"
    con = sqlite3.connect(db)
    con.execute("create table t (v text)")
    con.execute("insert into t values ('kept')")
    con.commit()
    con.close()
    t0 = datetime(2026, 11, 10)
    paths = [backup_sqlite(db, tmp_path / "b", t0 + timedelta(days=i), keep=3) for i in range(5)]
    assert sorted((tmp_path / "b").glob("*.db")) == paths[-3:]
    db.unlink()
    restore_sqlite(paths[-1], db)
    assert sqlite3.connect(db).execute("select v from t").fetchone() == ("kept",)


def test_sqlite_path_only_for_sqlite():
    assert sqlite_path("sqlite+aiosqlite:///data/x.db").name == "x.db"
    assert sqlite_path("postgresql+asyncpg://u@h/db") is None


def test_missing_bot_token_is_a_clear_error(monkeypatch, capsys):
    from app.config import load_settings
    monkeypatch.setattr(app_main, "load_settings", lambda: load_settings({}))
    assert app_main.main() == 2
    err = capsys.readouterr().err
    assert "BOT_TOKEN" in err and "Traceback" not in err


async def test_network_outage_keeps_listing_and_task(sessions):
    """T9.2: provider unreachable -> listing stays stored, its task waits instead of being lost."""
    from app.database.models import User, UserFilter
    from tests.tests_support import build_pipeline_with_page
    pipe, scanner = build_pipeline_with_page(sessions, MockProvider(default=OwnerStatus.UNAVAILABLE))
    async with sessions.begin() as s:
        u = User(telegram_id=1, filters=[UserFilter(collections=["IonGem"])])
        s.add(u)
    await scanner.run_cycle()
    while await pipe.process_one():
        pass
    async with sessions() as s:
        listings = (await s.execute(select(Listing).where(Listing.owner_lookup_status == "DEFERRED"))).scalars().all()
        tasks = (await s.execute(select(ProcessingTask).where(ProcessingTask.status == "retry"))).scalars().all()
    assert listings and len(tasks) == len(listings)
