from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database.models import Gift, Listing, ProcessingTask, User, UserFilter
from app.database.session import migrate

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
BIG_ID = 5922496306156144825  # > 2**53: must survive without float rounding


def gift(**kw) -> Gift:
    data = dict(telegram_unique_id=BIG_ID, gift_type_id=5825895989088617224, slug="IonGem-3185", collection="IonGem",
                number=3185, raw_data={"slug": "IonGem-3185"})
    return Gift(**{**data, **kw})


def listing(g: Gift, episode: int = 1, active: bool = True) -> Listing:
    return Listing(gift=g, listing_identity=f"{g.slug}:{episode}", episode=episode, price=Decimal("123.240000001"),
                   currency="TON", is_active=active, listing_date=NOW, first_seen_at=NOW, last_seen_at=NOW, raw_data={})


async def test_big_ids_decimal_and_utc_round_trip(sessions):
    async with sessions() as s:
        g = gift()
        s.add_all([g, listing(g)])
        await s.commit()
    async with sessions() as s:
        row = (await s.execute(select(Listing))).scalar_one()
        g2 = await s.get(Gift, row.gift_id)
    assert g2.telegram_unique_id == BIG_ID
    assert row.price == Decimal("123.240000001")
    assert row.first_seen_at == NOW and row.first_seen_at.tzinfo is not None


async def test_update_changes_updated_at(sessions):
    async with sessions() as s:
        g = gift()
        s.add(g)
        await s.commit()
        before = g.updated_at
        g.model = "Garnet"
        await s.commit()
        assert g.updated_at >= before and g.model == "Garnet"


async def test_duplicate_slug_rejected(sessions):
    async with sessions() as s:
        s.add_all([gift(), gift(telegram_unique_id=1)])
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_only_one_active_listing_per_gift(sessions):
    async with sessions() as s:
        g = gift()
        s.add_all([g, listing(g, 1, active=False), listing(g, 2)])
        await s.commit()
        s.add(listing(g, 3))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_failed_transaction_rolls_back_everything(sessions):
    async with sessions() as s:
        s.add(gift())
        await s.commit()
    async with sessions() as s:
        s.add(User(telegram_id=42))
        s.add(gift(telegram_unique_id=2))  # same slug -> whole transaction fails
        with pytest.raises(IntegrityError):
            await s.commit()
    async with sessions() as s:
        assert (await s.execute(select(User))).first() is None


async def test_queue_dedup_only_blocks_active_tasks(sessions):
    async with sessions() as s:
        s.add(ProcessingTask(task_type="owner_lookup", dedup_key="owner:IonGem-3185", status="completed"))
        s.add(ProcessingTask(task_type="owner_lookup", dedup_key="owner:IonGem-3185", status="pending"))
        await s.commit()
        s.add(ProcessingTask(task_type="owner_lookup", dedup_key="owner:IonGem-3185", status="retry"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_user_filter_defaults_and_cascade(sessions):
    async with sessions() as s:
        u = User(telegram_id=8556771897, filters=[UserFilter(collections=["IonGem"], price_max=Decimal("10.5"))])
        s.add(u)
        await s.commit()
        f = u.filters[0]
        assert f.notify_new_listing and not f.notify_price_changed and not f.require_username
        await s.delete(u)
        await s.commit()
        assert (await s.execute(select(UserFilter))).first() is None


async def test_alembic_migration_matches_models(tmp_path):
    from alembic import command
    from alembic.config import Config

    from app.database.session import ALEMBIC_INI

    url = f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}"
    await migrate(url)
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    import asyncio
    await asyncio.to_thread(command.check, cfg)  # raises if models and migrations differ
