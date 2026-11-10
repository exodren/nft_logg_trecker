import copy
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from telethon.errors import FloodWaitError

from app.config import load_settings
from app.database.models import Gift, Listing, ScanState
from app.mtproto.client import FloodWaitTooLong, MTProtoClient, MTProtoError, NotAuthorized
from app.mtproto.scanner import ListingScanner
from app.services import listings as svc

FIX = Path(__file__).parent / "fixtures" / "resale"
TYPE_ID = 5843762284240831056
T0 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FakeTelegram:
    """Answers GetStarGifts / GetResaleStarGifts from fixture dicts."""

    def __init__(self, pages: dict[str, dict]) -> None:
        self.pages = pages
        self.calls = []

    async def __call__(self, request):
        self.calls.append(type(request).__name__)
        if type(request).__name__ == "GetStarGiftsRequest":
            return {"_": "StarGifts", "gifts": [{"_": "StarGift", "id": TYPE_ID, "title": "Ion Gem", "availability_resale": 5}]}
        return copy.deepcopy(self.pages[request.offset])


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def scanner(sessions, pages, pages_per_type=1, hook=None, clock=None):
    return ListingScanner(FakeTelegram(pages), sessions, 60, on_events=hook, pages_per_type=pages_per_type,
                          clock=clock or Clock())


async def count(sessions, model):
    async with sessions() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar()


async def test_new_nfts_are_stored_once(sessions):
    p0 = load("5843762284240831056_p0.json")
    sc = scanner(sessions, {"": p0})
    stats = await sc.run_cycle()
    assert stats.new_listings == len({g["slug"] for g in p0["gifts"]}) == await count(sessions, Gift)
    again = await sc.run_cycle()  # same page again
    assert again.new_listings == 0 and again.duplicates == stats.received
    assert await count(sessions, Listing) == stats.new_listings


async def test_pagination_follows_offset(sessions):
    p0, p1 = load("5843762284240831056_p0.json"), load("5843762284240831056_p1.json")
    stats = await scanner(sessions, {"": p0, p0["next_offset"]: p1}, pages_per_type=2).run_cycle()
    assert stats.pages == 2 and stats.new_listings == 100


async def test_offset_loop_guard_stops_on_empty_page(sessions):
    p0 = load("5843762284240831056_p0.json")
    bad = {**p0, "gifts": [], "next_offset": "garbage"}
    stats = await scanner(sessions, {"": p0, p0["next_offset"]: bad, "garbage": bad}, pages_per_type=10).run_cycle()
    assert stats.pages == 2


async def test_empty_type_is_fully_scanned(sessions):
    stats = await scanner(sessions, {"": {"_": "ResaleStarGifts", "count": 0, "gifts": [], "next_offset": None}}).run_cycle()
    assert stats.new_listings == 0 and stats.stopped is None
    async with sessions() as s:
        st = (await s.execute(select(ScanState))).scalar_one()
    assert st.last_full_scan_at == T0 and st.last_count == 0


async def test_price_change_updates_listing_not_gift(sessions):
    p0 = load("5843762284240831056_p0.json")
    sc = scanner(sessions, {"": p0})
    await sc.run_cycle()
    changed = copy.deepcopy(p0)
    g = next(x for x in changed["gifts"] if x["slug"] == "IonGem-3185")
    g["resell_amount"][0]["amount"] = 9000
    sc._client.pages[""] = changed
    events = []

    async def hook(s, evs):
        events.extend(evs)
    sc._on_events = hook
    stats = await sc.run_cycle()
    assert stats.price_changes == 1 and stats.new_listings == 0
    pc = next(e for e in events if e.type == svc.PRICE_CHANGED)
    assert (pc.old_price, pc.new_price) == (Decimal(8755), Decimal(9000))
    assert await count(sessions, Gift) == len({x["slug"] for x in p0["gifts"]})


async def test_converted_currency_drift_is_not_a_price_change(sessions):
    p0 = load("5843762284240831056_p0.json")
    sc = scanner(sessions, {"": p0})
    await sc.run_cycle()
    drift = copy.deepcopy(p0)
    for g in drift["gifts"]:
        if not g.get("resale_ton_only"):
            g["resell_amount"][1]["amount"] += 10_000_000
    sc._client.pages[""] = drift
    assert (await sc.run_cycle()).price_changes == 0


async def test_disappearance_marks_inactive_only_after_full_scan(sessions):
    p0 = load("5843762284240831056_p0.json")
    small = {**p0, "gifts": p0["gifts"][:3], "next_offset": None}
    sc = scanner(sessions, {"": small})
    await sc.run_cycle()
    sc._client.pages[""] = {**small, "gifts": small["gifts"][:2]}
    stats = await sc.run_cycle()
    assert stats.inactive == 1
    async with sessions() as s:
        gone = (await s.execute(select(Listing).where(Listing.is_active.is_(False)))).scalar_one()
    assert gone.state == "INACTIVE" and gone.removed_at is not None


async def test_partial_scan_never_marks_inactive_but_stale_becomes_unknown(sessions):
    p0 = load("5843762284240831056_p0.json")
    clock = Clock()
    sc = scanner(sessions, {"": p0}, clock=clock)
    await sc.run_cycle()
    sc._client.pages[""] = {**p0, "gifts": p0["gifts"][1:]}
    clock.now = T0 + timedelta(hours=25)
    stats = await sc.run_cycle()
    assert stats.inactive == 0
    async with sessions() as s:
        states = {l.state for l in (await s.execute(select(Listing))).scalars()}
    assert "UNKNOWN" in states and "INACTIVE" not in states


async def test_relist_after_inactive_and_seller_change_open_new_episode(sessions):
    p0 = load("5843762284240831056_p0.json")
    one = {**p0, "gifts": [p0["gifts"][0]], "next_offset": None}
    sc = scanner(sessions, {"": one})
    await sc.run_cycle()
    sc._client.pages[""] = {**one, "gifts": []}
    await sc.run_cycle()  # gone -> INACTIVE
    sc._client.pages[""] = one
    await sc.run_cycle()  # back -> episode 2
    other = copy.deepcopy(one)
    other["gifts"][0]["owner_name"] = "Another Seller"
    sc._client.pages[""] = other
    await sc.run_cycle()  # new seller -> episode 3
    async with sessions() as s:
        ids = sorted(l.listing_identity for l in (await s.execute(select(Listing))).scalars())
    slug = one["gifts"][0]["slug"]
    assert ids == [f"{slug}:1", f"{slug}:2", f"{slug}:3"]


async def test_hook_runs_in_same_transaction(sessions):
    p0 = load("5843762284240831056_p0.json")

    async def failing_hook(s, evs):
        raise RuntimeError("downstream failed")
    stats = await scanner(sessions, {"": p0}, hook=failing_hook).run_cycle()
    assert stats.types_failed == 1 and await count(sessions, Listing) == 0  # rolled back, will be rescanned


# --- MTProto client -------------------------------------------------------------------------------

class FakeTelethon:
    def __init__(self, *args, outcomes=(), authorized=True, **kwargs):
        self.outcomes = list(outcomes)
        self.authorized = authorized
        self.connects = 0
        self.connected = False

    async def connect(self):
        self.connects += 1
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authorized

    async def __call__(self, request):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class Req:
    pass


def client_with(tmp_path, **fake_kwargs):
    settings = load_settings({"API_ID": "1", "API_HASH": "h", "TELEGRAM_SESSION": str(tmp_path / "app.session")})
    (tmp_path / "app.session").write_bytes(b"")
    holder = {}

    def factory(*a, **kw):
        holder["t"] = FakeTelethon(*a, **{**kw, **fake_kwargs})
        return holder["t"]

    async def no_sleep(_):
        return None
    return MTProtoClient(settings, client_factory=factory, sleep=no_sleep, delay_s=0), holder


def req(name):
    return type(name, (), {})()


async def test_client_rejects_write_methods(tmp_path):
    c, _ = client_with(tmp_path, outcomes=[])
    await c.connect()
    with pytest.raises(PermissionError):
        await c(req("SendStarGiftOfferRequest"))


async def test_client_retries_flood_wait_then_succeeds(tmp_path):
    c, _ = client_with(tmp_path, outcomes=[FloodWaitError(request=None, capture=5), "ok"])
    await c.connect()
    assert await c(req("GetResaleStarGiftsRequest")) == "ok" and c.stats["flood_wait"] == 1


async def test_client_long_flood_wait_raises(tmp_path):
    c, _ = client_with(tmp_path, outcomes=[FloodWaitError(request=None, capture=3600)])
    await c.connect()
    with pytest.raises(FloodWaitTooLong):
        await c(req("GetResaleStarGiftsRequest"))


async def test_client_reconnects_on_network_error_and_gives_up(tmp_path):
    c, holder = client_with(tmp_path, outcomes=[ConnectionError(), "ok"])
    await c.connect()
    assert await c(req("GetStarGiftsRequest")) == "ok" and c.stats["reconnects"] == 1
    holder["t"].outcomes = [OSError(), OSError(), OSError()]
    with pytest.raises(MTProtoError):
        await c(req("GetStarGiftsRequest"))


async def test_client_never_prompts_for_login(tmp_path):
    c, _ = client_with(tmp_path, authorized=False)
    with pytest.raises(NotAuthorized):
        await c.connect()


async def test_scanner_stops_cycle_on_mtproto_failure(sessions):
    class Broken:
        async def __call__(self, request):
            if type(request).__name__ == "GetStarGiftsRequest":
                return {"gifts": [{"_": "StarGift", "id": 1, "availability_resale": 1},
                                  {"_": "StarGift", "id": 2, "availability_resale": 1}]}
            raise MTProtoError("network retries exhausted")
    sc = ListingScanner(Broken(), sessions, 60, clock=Clock())
    stats = await sc.run_cycle()
    assert stats.stopped == "MTProtoError" and stats.types == 1 and sc.status.last_error
