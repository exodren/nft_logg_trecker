"""Provider over a local aiohttp server: real status codes and headers, no network, no paid requests."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from sqlalchemy import select

from app.database.models import ApiUsage, Gift, Owner, OwnerLookupCache
from app.providers.base import Billing, OwnerData, OwnerStatus
from app.providers.kartoshka import KartoshkaApiProvider, OwnerFields, parse_billing
from app.providers.mock import MockProvider
from app.services.api_usage import ApiUsageManager
from app.services.enrichment import OwnerResolver

TOKEN = "12:secret-token-value-123456"
NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


class VerifiedTestMapper:
    """Stands in for the real mapper (K4.1b) and only reads keys this test server itself sends."""
    verified = True

    def __call__(self, result):
        o = result["test_owner"]
        if o is None:
            return OwnerFields(OwnerStatus.NOT_FOUND)
        if o.get("hidden"):
            return OwnerFields(OwnerStatus.HIDDEN, is_hidden=True)
        return OwnerFields(OwnerStatus.FOUND, telegram_id=o.get("id"), username=o.get("username"))


def billing_headers(free="5", cost="0", balance="1.5"):
    return {"X-Free-Remaining": free, "X-Request-Cost": cost, "X-Balance": balance}


@pytest.fixture
async def server():
    state = {"calls": [], "plan": {}}

    async def gift(request: web.Request):
        state["calls"].append((request.match_info["ref"], request.headers.get("Authorization")))
        plan = state["plan"].get(request.match_info["ref"], [])
        status, body, headers = plan.pop(0) if plan else (404, {"ok": False, "error_code": 404}, {})
        return web.json_response(body, status=status, headers=headers)

    async def ping(request):
        return web.json_response({"ok": True, "result": {}}, headers=billing_headers(free="100", cost="0"))

    app = web.Application()
    app.router.add_get("/v1/gift/{ref}", gift)
    app.router.add_get("/v1/ping", ping)
    srv = TestServer(app)
    await srv.start_server()
    state["url"] = str(srv.make_url("/v1"))
    yield state
    await srv.close()


async def no_sleep(_):
    return None


def provider(server, mapper=None) -> KartoshkaApiProvider:
    return KartoshkaApiProvider(TOKEN, base_url=server["url"], mapper=mapper or VerifiedTestMapper(),
                                sleep=no_sleep, min_interval_s=0)


def ok(owner, **h):
    return (200, {"ok": True, "result": {"slug": "IonGem-3185", "test_owner": owner}}, billing_headers(**h))


async def test_found_owner_with_username_and_bearer_token(server):
    server["plan"]["IonGem-3185"] = [ok({"id": 8556771897, "username": "tgmefchik"})]
    p = provider(server)
    data = await p.lookup("IonGem-3185")
    await p.close()
    assert data.status is OwnerStatus.FOUND and data.telegram_id == 8556771897 and data.username == "tgmefchik"
    assert data.has_username and data.has_telegram_id
    assert server["calls"][0][1] == f"Bearer {TOKEN}"
    assert data.billing == Billing(5, Decimal("0"), Decimal("1.5"))


async def test_owner_without_username_keeps_telegram_id(server):
    server["plan"]["A-1"] = [ok({"id": 42, "username": None})]
    p = provider(server)
    data = await p.lookup("A-1")
    await p.close()
    assert data.status is OwnerStatus.FOUND and data.telegram_id == 42 and not data.has_username


async def test_hidden_owner(server):
    server["plan"]["A-1"] = [ok({"hidden": True})]
    p = provider(server)
    data = await p.lookup("A-1")
    await p.close()
    assert data.status is OwnerStatus.HIDDEN and data.is_hidden and data.telegram_id is None


async def test_missing_gift_is_not_found(server):
    p = provider(server)
    data = await p.lookup("Nope-1")
    await p.close()
    assert data.status is OwnerStatus.NOT_FOUND and data.http_status == 404


@pytest.mark.parametrize("status,expected,reason", [
    (400, OwnerStatus.ERROR, "bad_request"),
    (401, OwnerStatus.ERROR, "unauthorized"),
    (402, OwnerStatus.UNAVAILABLE, "payment_required"),
])
async def test_error_codes(server, status, expected, reason):
    server["plan"]["A-1"] = [(status, {"ok": False, "error_code": status}, {})]
    p = provider(server)
    data = await p.lookup("A-1")
    await p.close()
    assert (data.status, data.reason) == (expected, reason)
    assert len(server["calls"]) == 1  # never retried
    assert p.ready is (status != 401)


async def test_429_waits_retry_after_then_succeeds(server):
    waits = []

    async def sleep(s):
        waits.append(s)
    server["plan"]["A-1"] = [(429, {"ok": False}, {"Retry-After": "7"}), ok({"id": 1})]
    p = KartoshkaApiProvider(TOKEN, base_url=server["url"], mapper=VerifiedTestMapper(), sleep=sleep, min_interval_s=0)
    data = await p.lookup("A-1")
    await p.close()
    assert data.status is OwnerStatus.FOUND and 7.0 in waits


async def test_server_errors_are_bounded(server):
    server["plan"]["A-1"] = [(500, {"ok": False}, {})] * 5
    p = provider(server)
    data = await p.lookup("A-1")
    await p.close()
    assert (data.status, data.reason) == (OwnerStatus.UNAVAILABLE, "http_500") and len(server["calls"]) == 3


async def test_malformed_200_is_error(server):
    server["plan"]["A-1"] = [(200, {"unexpected": True}, {})]
    p = provider(server)
    data = await p.lookup("A-1")
    await p.close()
    assert (data.status, data.reason) == (OwnerStatus.ERROR, "malformed")


async def test_network_failure_is_unavailable():
    p = KartoshkaApiProvider(TOKEN, base_url="http://127.0.0.1:9/v1", mapper=VerifiedTestMapper(), sleep=no_sleep,
                             min_interval_s=0)
    data = await p.lookup("A-1")
    await p.close()
    assert (data.status, data.reason) == (OwnerStatus.UNAVAILABLE, "network")


async def test_unverified_mapper_never_guesses_fields(server):
    server["plan"]["A-1"] = [ok({"id": 1, "username": "x"})]
    p = provider(server, mapper=None)
    p._mapper = __import__("app.providers.kartoshka", fromlist=["x"]).UnverifiedSchemaMapper()
    assert not p.ready
    data = await p.lookup("A-1")
    await p.close()
    assert (data.status, data.reason) == (OwnerStatus.UNAVAILABLE, "schema_unverified") and data.raw is not None


def test_parse_billing_tolerates_missing_headers():
    assert parse_billing({}) == Billing()
    assert parse_billing({"X-Request-Cost": "abc", "X-Free-Remaining": "x"}) == Billing()


# --- usage manager, cache and resolver ------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


async def add_gift(sessions, slug="IonGem-3185", uid=1):
    async with sessions.begin() as s:
        g = Gift(telegram_unique_id=uid, gift_type_id=2, slug=slug, collection=slug.split("-")[0],
                 number=int(slug.split("-")[1]), raw_data={})
        s.add(g)
    return g.id


def answer(status, http=200, cost="0", free=5, **kw):
    return OwnerData(status=status, provider="kartoshka", fetched_at=NOW, http_status=http,
                     billing=Billing(free, Decimal(cost), Decimal("1")), **kw)


async def test_usage_counts_free_paid_errors_and_report(sessions):
    u = ApiUsageManager(sessions, "kartoshka", Decimal("1"), Decimal("0.00005"), clock=Clock())
    await u.record(answer(OwnerStatus.FOUND, cost="0", free=1))
    await u.record(answer(OwnerStatus.FOUND, cost="0.00005", free=0))
    await u.record(answer(OwnerStatus.NOT_FOUND, http=404, cost="0", free=0))
    await u.record(OwnerData(OwnerStatus.UNAVAILABLE, "kartoshka", NOW, http_status=500, reason="http_500"))
    r = await u.report()
    assert (r.requests, r.success, r.errors, r.free, r.paid) == (4, 2, 1, 1, 1)
    assert r.spent_ton == Decimal("0.00005") and r.average_cost_ton == Decimal("0.00005")
    assert r.remaining_budget_ton == Decimal("0.99995")


async def test_zero_budget_means_free_quota_only(sessions):
    u = ApiUsageManager(sessions, "kartoshka", Decimal("0"), Decimal("0.00005"), clock=Clock())
    assert (await u.can_spend()).reason == "quota_unknown"
    await u.record_probe(Billing(free_remaining=1))
    assert (await u.can_spend()).allowed
    d = await u.can_spend()  # the single free request is reserved
    assert not d.allowed and d.reason == "free_quota_exhausted" and d.retry_at == datetime(2026, 11, 11, tzinfo=timezone.utc)


async def test_budget_warning_and_stop_notify_admin(sessions):
    sent = []

    async def notify(msg):
        sent.append(msg)
    u = ApiUsageManager(sessions, "kartoshka", Decimal("0.0001"), Decimal("0.00005"), notify_admin=notify, clock=Clock())
    await u.record_probe(Billing(free_remaining=0))
    assert (await u.can_spend()).allowed
    await u.record(answer(OwnerStatus.FOUND, cost="0.00005", free=0))
    await u.record(answer(OwnerStatus.FOUND, cost="0.00005", free=0))
    d = await u.can_spend()
    assert not d.allowed and d.reason == "budget_exhausted"
    assert any("потрачено" in m for m in sent) and any("исчерпан" in m for m in sent)


async def test_402_stops_paid_requests_and_alerts(sessions):
    sent = []

    async def notify(msg):
        sent.append(msg)
    u = ApiUsageManager(sessions, "kartoshka", Decimal("5"), Decimal("0.00005"), notify_admin=notify, clock=Clock())
    await u.record(OwnerData(OwnerStatus.UNAVAILABLE, "kartoshka", NOW, http_status=402, reason="payment_required"))
    d = await u.can_spend()
    assert not d.allowed and d.reason == "payment_required" and len(sent) == 1
    await u.resume()
    assert (await u.can_spend()).reason == "quota_unknown"


async def test_usage_resets_on_new_utc_day(sessions):
    clock = Clock()
    u = ApiUsageManager(sessions, "kartoshka", Decimal("0"), None, clock=clock)
    await u.record_probe(Billing(free_remaining=0))
    assert not (await u.can_spend()).allowed
    clock.now = NOW + timedelta(days=1)
    assert (await u.can_spend()).reason == "quota_unknown"
    async with sessions() as s:
        assert len((await s.execute(select(ApiUsage))).scalars().all()) == 2


async def test_resolver_uses_cache_and_never_pays_twice(sessions):
    gid = await add_gift(sessions)
    mock = MockProvider({"IonGem-3185": OwnerData(OwnerStatus.FOUND, "mock", NOW, telegram_id=7, username="u")})
    r = OwnerResolver(sessions, mock, None, cache_ttl_s=3600, clock=Clock())
    first, second = await r.resolve(gid), await r.resolve(gid)
    assert (first.source, second.source) == ("api", "cache") and mock.calls == ["IonGem-3185"]
    assert second.data.username == "u" and second.data.telegram_id == 7
    async with sessions() as s:
        assert (await s.execute(select(Owner))).scalar_one().telegram_id == 7


async def test_cache_expires_after_ttl(sessions):
    gid = await add_gift(sessions)
    clock = Clock()
    mock = MockProvider(default=OwnerStatus.HIDDEN)
    r = OwnerResolver(sessions, mock, None, cache_ttl_s=60, clock=clock)
    await r.resolve(gid)
    clock.now = NOW + timedelta(seconds=61)
    await r.resolve(gid)
    assert len(mock.calls) == 2


async def test_unavailable_is_not_cached(sessions):
    gid = await add_gift(sessions)
    mock = MockProvider(default=OwnerStatus.UNAVAILABLE)
    r = OwnerResolver(sessions, mock, None, cache_ttl_s=3600, clock=Clock())
    await r.resolve(gid)
    await r.resolve(gid)
    assert len(mock.calls) == 2


async def test_resolver_defers_when_budget_denies_and_skips_disabled_provider(sessions):
    gid = await add_gift(sessions)
    u = ApiUsageManager(sessions, "kartoshka", Decimal("0"), None, clock=Clock())
    await u.record_probe(Billing(free_remaining=0))
    mock = MockProvider(default=OwnerStatus.FOUND)
    res = await OwnerResolver(sessions, mock, u, 3600, clock=Clock()).resolve(gid)
    assert res.source == "deferred" and res.deferred and mock.calls == []
    res2 = await OwnerResolver(sessions, None, u, 3600, clock=Clock()).resolve(gid)
    assert res2.source == "disabled"


async def test_resolver_probes_quota_before_first_request(server, sessions):
    gid = await add_gift(sessions)
    server["plan"]["IonGem-3185"] = [ok({"id": 5, "username": "z"}, free="99")]
    p = provider(server)
    u = ApiUsageManager(sessions, "kartoshka", Decimal("0"), None, clock=Clock())
    res = await OwnerResolver(sessions, p, u, 3600, clock=Clock()).resolve(gid)
    await p.close()
    assert res.data.status is OwnerStatus.FOUND
    assert (await u.report()).free_remaining == 99
    async with sessions() as s:
        assert (await s.execute(select(OwnerLookupCache))).scalar_one().status == "FOUND"
