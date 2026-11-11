"""Many concurrent read-then-write transactions on a file DB must queue (BEGIN IMMEDIATE), never 'database is locked'."""
import asyncio

from sqlalchemy import text

from app.database.session import create_engine, session_factory


async def test_concurrent_read_then_write_transactions_do_not_lock(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'c.db').as_posix()}")
    sessions = session_factory(engine)
    async with sessions.begin() as s:
        await s.execute(text("CREATE TABLE t (n INTEGER)"))

    async def writer(i: int) -> None:
        async with sessions.begin() as s:
            await s.execute(text("SELECT count(*) FROM t"))  # read first: a plain BEGIN fails on the upgrade
            await asyncio.sleep(0.01)
            await s.execute(text("INSERT INTO t VALUES (:n)"), {"n": i})

    try:
        await asyncio.gather(*(writer(i) for i in range(30)))
        async with sessions() as s:
            assert (await s.execute(text("SELECT count(*) FROM t"))).scalar() == 30
    finally:
        await engine.dispose()


async def test_writers_run_one_at_a_time_and_nested_begin_raises(sessions):
    import pytest
    active = peak = 0

    async def writer() -> None:
        nonlocal active, peak
        async with sessions.begin():
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.005)
            active -= 1

    await asyncio.gather(*(writer() for _ in range(10)))
    assert peak == 1
    with pytest.raises(RuntimeError, match="nested"):
        async with sessions.begin():
            async with sessions.begin():
                pass
