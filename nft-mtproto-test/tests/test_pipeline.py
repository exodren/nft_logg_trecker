import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.database.models import Listing, Notification, ProcessingTask, User, UserFilter
from app.mtproto.scanner import ListingScanner
from app.providers.base import Billing, OwnerData, OwnerStatus
from app.providers.mock import MockProvider
from app.services.api_usage import ApiUsageManager
from app.services.enrichment import OwnerResolver
from app.services.filters import ListingView, stage1, stage2
from app.services.pipeline import Pipeline
from app.services.queue import HIGH, LOW, TaskQueue, enqueue

FIX = Path(__file__).parent / "fixtures" / "resale"
T0 = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
SLUG = "IonGem-3185"


# --- pure filters ----------------------------------------------------------------------------------

@dataclass
class F:
    collections: list = field(default_factory=list)
    models: list = field(default_factory=list)
    backdrops: list = field(default_factory=list)
    patterns: list = field(default_factory=list)
    price_currency: str = "XTR"
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    require_username: bool = False
    require_telegram_id: bool = False
    owner_usernames: list = field(default_factory=list)
    owner_telegram_ids: list = field(default_factory=list)


V = ListingView("IonGem", "Ion Gem", "Garnet", "Indigo Dye", "Potion", Decimal(8755), Decimal("123.24"))


@pytest.mark.parametrize("f,expected", [
    (F(), True),
    (F(collections=["Ion Gem"]), True), (F(collections=["iongem"]), True), (F(collections=["MoonPendant"]), False),
    (F(models=["garnet"]), True), (F(models=["Ruby"]), False),
    (F(backdrops=["Indigo Dye"]), True), (F(patterns=["Skull"]), False),
    (F(price_min=Decimal(8000), price_max=Decimal(9000)), True), (F(price_max=Decimal(8000)), False),
    (F(price_currency="TON", price_min=Decimal("123.24")), True), (F(price_currency="TON", price_max=Decimal(100)), False),
    (F(require_username=True, require_telegram_id=True), True),  # owner criteria are not stage 1
])
def test_stage1(f, expected):
    assert stage1(f, V) is expected


def test_stage1_missing_price_never_matches_a_price_range():
    assert not stage1(F(price_min=Decimal(1)), ListingView("A", None, None, None, None, None, None))


def owner(status, tid=None, username=None):
    return OwnerData(status=status, provider="mock", fetched_at=T0, telegram_id=tid, username=username)


@pytest.mark.parametrize("status", list(OwnerStatus))
def test_stage2_matrix(status):
    o = owner(status, 1, "u") if status is OwnerStatus.FOUND else owner(status)
    assert stage2(F(), o) is True  # no owner criteria: always
    assert stage2(F(require_username=True), o) is (status is OwnerStatus.FOUND)
    assert stage2(F(require_telegram_id=True), o) is (status is OwnerStatus.FOUND)
    assert stage2(F(require_username=True), None) is False


def test_stage2_username_and_id_individually():
    assert not stage2(F(require_username=True), owner(OwnerStatus.FOUND, tid=1))
    assert not stage2(F(require_telegram_id=True), owner(OwnerStatus.FOUND, tid=1))  # ID without username: no
    assert stage2(F(require_telegram_id=True), owner(OwnerStatus.FOUND, 1, "u"))
    assert not stage2(F(require_telegram_id=True), owner(OwnerStatus.FOUND, username="u"))


# --- queue ----------------------------------------------------------------------------------------

async def test_queue_priority_dedup_retry_and_recovery(sessions):
    q = TaskQueue(sessions)
    async with sessions.begin() as s:
        await enqueue(s, "owner_lookup", "owner:1", priority=LOW, payload={"backfill": True})
        await enqueue(s, "owner_lookup", "owner:2", priority=HIGH)
        merged = await enqueue(s, "owner_lookup", "owner:1", priority=HIGH, payload={"notify": True})
    assert merged.priority == HIGH and merged.payload == {"backfill": True, "notify": True}
    first = await q.claim()
    assert first.dedup_key == "owner:1"  # same priority now, older first
    second = await q.claim()
    assert await q.claim() is None
    assert await q.recover() == 2  # crash: both back to pending
    t = await q.claim()
    assert await q.fail(t.id, "boom") == "retry"
    async with sessions.begin() as s:
        row = await s.get(ProcessingTask, t.id)
        row.attempts, row.max_attempts, row.next_attempt_at = 5, 5, T0 - timedelta(days=1)
    assert await q.fail(t.id, "boom") == "failed"
    assert await q.retry_failed() == 1


# --- pipeline -------------------------------------------------------------------------------------

def page() -> dict:
    return json.loads((FIX / "5843762284240831056_p0.json").read_text(encoding="utf-8"))


class FakeTelegram:
    def __init__(self, p):
        self.p = p

    async def __call__(self, request):
        if type(request).__name__ == "GetStarGiftsRequest":
            return {"gifts": [{"_": "StarGift", "id": 5843762284240831056, "availability_resale": 1}]}
        return copy.deepcopy(self.p)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


async def add_user(sessions, tid, **filter_kw):
    async with sessions.begin() as s:
        u = User(telegram_id=tid, filters=[UserFilter(**filter_kw)])
        s.add(u)
    return u.id


def build(sessions, provider, usage=None, clock=None):
    clock = clock or Clock()
    queue = TaskQueue(sessions, clock=clock)
    resolver = OwnerResolver(sessions, provider, usage, cache_ttl_s=3600, clock=clock)
    pipe = Pipeline(sessions, queue, resolver, clock=clock)
    scanner = ListingScanner(FakeTelegram(page()), sessions, 60, on_events=pipe.on_events, clock=clock)
    return pipe, scanner


async def drain(pipe):
    while await pipe.process_one():
        pass


async def notified(sessions):
    async with sessions() as s:
        rows = (await s.execute(select(User.telegram_id, Notification.event_key).join(User))).all()
    return sorted(rows)


async def listing_status(sessions, slug=SLUG):
    async with sessions() as s:
        rows = (await s.execute(select(Listing))).scalars().all()
    return {l.listing_identity.split(":")[0]: l.owner_lookup_status for l in rows}[slug]


async def test_no_matching_user_means_no_paid_request_but_listing_saved(sessions):
    mock = MockProvider(default=OwnerStatus.FOUND)
    pipe, scanner = build(sessions, mock)
    await add_user(sessions, 1, collections=["MoonPendant"])
    stats = await scanner.run_cycle()
    await drain(pipe)
    assert stats.new_listings == 50 and mock.calls == []
    assert await listing_status(sessions) == "NOT_NEEDED"


async def test_matching_listing_is_looked_up_once_and_notified(sessions):
    mock = MockProvider({SLUG: owner(OwnerStatus.FOUND, 8556771897, "tgmefchik")})
    pipe, scanner = build(sessions, mock)
    await add_user(sessions, 1, collections=["IonGem"], price_max=Decimal(8755))
    await scanner.run_cycle()
    await drain(pipe)
    assert SLUG in mock.calls and len(mock.calls) == len(set(mock.calls))
    assert (1, f"NEW_LISTING:{await _listing_id(sessions)}") in await notified(sessions)
    await scanner.run_cycle()  # same page again: no new events, no new requests
    await drain(pipe)
    assert mock.calls.count(SLUG) == 1


async def _listing_id(sessions, slug=SLUG):
    async with sessions() as s:
        return (await s.execute(select(Listing.id).where(Listing.listing_identity == f"{slug}:1"))).scalar_one()


async def test_stage2_owner_criteria_without_false_matches(sessions):
    mock = MockProvider({SLUG: owner(OwnerStatus.FOUND, tid=42)}, default=OwnerStatus.HIDDEN)
    pipe, scanner = build(sessions, mock)
    await add_user(sessions, 1, collections=["IonGem"], require_username=True)
    await add_user(sessions, 2, collections=["IonGem"], require_telegram_id=True)
    await add_user(sessions, 3, collections=["IonGem"])
    await scanner.run_cycle()
    await drain(pipe)
    got = await notified(sessions)
    lid = await _listing_id(sessions)
    key = f"NEW_LISTING:{lid}"
    # The owner has a Telegram ID but no username: it is not a usable owner, so no owner criterion matches.
    assert (1, key) not in got and (2, key) not in got and (3, key) in got
    assert not {t for t, _ in got} & {1, 2}  # HIDDEN owners of other NFTs never match either


async def test_deferred_budget_keeps_listing_and_resumes_without_duplicates(sessions):
    clock = Clock()
    usage = ApiUsageManager(sessions, "mock", Decimal("0"), None, clock=clock)
    await usage.record_probe(Billing(free_remaining=0))
    mock = MockProvider({SLUG: owner(OwnerStatus.FOUND, 7, "seller")})
    pipe, scanner = build(sessions, mock, usage, clock)
    await add_user(sessions, 1, collections=["IonGem"], models=[_model()], require_username=True)
    await add_user(sessions, 2, collections=["IonGem"], models=[_model()])
    await scanner.run_cycle()
    await drain(pipe)
    lid = await _listing_id(sessions)
    key = f"NEW_LISTING:{lid}"
    assert mock.calls == [] and await listing_status(sessions) == "DEFERRED"
    assert [r for r in await notified(sessions) if r[1] == key] == [(2, key)]  # no owner data: user 1 waits
    assert all(t == 2 for t, _ in await notified(sessions))
    clock.now = T0 + timedelta(days=1, minutes=1)  # new UTC day: quota probe -> free again
    await usage.record_probe(Billing(free_remaining=100))
    await drain(pipe)
    assert SLUG in mock.calls and await listing_status(sessions) == "DONE"
    rows = await notified(sessions)
    assert [r for r in rows if r[1] == key] == [(1, key), (2, key)]
    assert len(rows) == len(set(rows))  # user 2 got nothing twice


def _model():
    return next(a["name"] for g in page()["gifts"] if g["slug"] == SLUG for a in g["attributes"]
                if a["_"] == "StarGiftAttributeModel")


async def test_backfill_for_new_filter_uses_cache_and_low_priority(sessions):
    mock = MockProvider(default=OwnerStatus.FOUND)
    pipe, scanner = build(sessions, mock)
    await scanner.run_cycle()  # no users yet: everything NOT_NEEDED, nothing paid
    assert mock.calls == []
    async with sessions.begin() as s:
        u = User(telegram_id=9, filters=[UserFilter(collections=["IonGem"], models=[_model()])])
        s.add(u)
    fid = u.filters[0].id
    queued = await pipe.backfill(fid)
    assert queued >= 1
    async with sessions() as s:
        prios = {t.priority for t in (await s.execute(select(ProcessingTask))).scalars()}
    assert prios == {LOW}
    await drain(pipe)
    calls = len(mock.calls)
    assert calls == queued and await notified(sessions) == []  # backfill does not push notifications
    assert await pipe.backfill(fid) == 0  # cached now: nothing more to pay for
    assert len(mock.calls) == calls


async def test_backfill_stops_on_budget_without_blocking(sessions):
    clock = Clock()
    usage = ApiUsageManager(sessions, "mock", Decimal("0"), None, clock=clock)
    await usage.record_probe(Billing(free_remaining=1))
    mock = MockProvider(default=OwnerStatus.FOUND)
    pipe, scanner = build(sessions, mock, usage, clock)
    await scanner.run_cycle()
    fid = await _filter_id(sessions, await add_user(sessions, 5, collections=["IonGem"]))
    queued = await pipe.backfill(fid)
    await drain(pipe)
    assert len(mock.calls) == 1 and queued > 1  # one free request, the rest deferred to tomorrow
    async with sessions() as s:
        deferred = (await s.execute(select(ProcessingTask).where(ProcessingTask.status == "retry"))).scalars().all()
    assert len(deferred) == queued - 1 and all(t.next_attempt_at.date() == (T0 + timedelta(days=1)).date() for t in deferred)


async def _filter_id(sessions, user_id):
    async with sessions() as s:
        return (await s.execute(select(UserFilter.id).where(UserFilter.user_id == user_id))).scalar_one()


async def test_inactive_listing_task_completes_without_request(sessions):
    mock = MockProvider(default=OwnerStatus.FOUND)
    pipe, scanner = build(sessions, mock)
    await add_user(sessions, 1, collections=["IonGem"])
    await scanner.run_cycle()
    async with sessions.begin() as s:
        for l in (await s.execute(select(Listing))).scalars():
            l.is_active, l.state = False, "INACTIVE"
    await drain(pipe)
    assert mock.calls == []
