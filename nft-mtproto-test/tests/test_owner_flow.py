"""NFT -> Kartoshka -> owner -> user filters -> notification. Owner values never select NFTs."""
import copy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.bot.handlers import FilterInputError, apply_filter_command
from app.database.models import Gift, Listing, Notification, User, UserFilter
from app.providers.base import OwnerData, OwnerStatus
from app.providers.mock import MockProvider
from app.services.enrichment import OwnerResolver
from app.services.filters import has_owner_criteria, stage1, stage2
from tests.test_pipeline import F, V, owner
from tests.tests_support import build_pipeline_with_page

SLUG = "IonGem-3185"
NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


# --- stage B: owner values ---------------------------------------------------------------------

@pytest.mark.parametrize("wanted,found,expected", [
    (["tgmefchik"], owner(OwnerStatus.FOUND, 1, "tgmefchik"), True),
    (["@TgMefChik"], owner(OwnerStatus.FOUND, 1, "tgmefchik"), True),  # @ and case ignored
    (["someone"], owner(OwnerStatus.FOUND, 1, "tgmefchik"), False),
    (["tgmefchik"], owner(OwnerStatus.FOUND, tid=1), False),  # no username is not a match
    (["tgmefchik"], owner(OwnerStatus.HIDDEN), False),
    (["tgmefchik"], owner(OwnerStatus.UNAVAILABLE), False),
    (["tgmefchik"], None, False),
])
def test_owner_username_value(wanted, found, expected):
    assert stage2(F(owner_usernames=wanted), found) is expected


def test_owner_telegram_id_value_and_combination():
    o = owner(OwnerStatus.FOUND, 8556771897, "tgmefchik")
    assert stage2(F(owner_telegram_ids=[8556771897]), o)
    assert not stage2(F(owner_telegram_ids=[1]), o)
    assert not stage2(F(owner_telegram_ids=[8556771897], owner_usernames=["other"]), o)  # all criteria must hold


def test_id_without_username_never_matches_any_owner_criterion():
    id_only = owner(OwnerStatus.FOUND, 8556771897, None)
    for f in (F(owner_telegram_ids=[8556771897]), F(require_telegram_id=True), F(owner_usernames=["x"])):
        assert not stage2(f, id_only)
    assert stage2(F(), id_only)  # users without owner criteria are unaffected


def test_apply_owner_rules():
    from app.providers.base import apply_owner_rules
    out = apply_owner_rules(owner(OwnerStatus.FOUND, 42, None))
    assert (out.status, out.telegram_id, out.username) == (OwnerStatus.NO_USERNAME, None, None)
    kept = owner(OwnerStatus.FOUND, 42, "name")
    assert apply_owner_rules(kept) is kept
    hidden = owner(OwnerStatus.HIDDEN)
    assert apply_owner_rules(hidden) is hidden


def test_owner_values_never_affect_stage_a():
    f = F(owner_usernames=["tgmefchik"], owner_telegram_ids=[1])
    assert has_owner_criteria(f) and stage1(f, V)  # NFT criteria empty -> every NFT is a candidate


def test_filter_owner_command():
    uf = UserFilter(collections=[], models=[], backdrops=[], patterns=[], owner_usernames=[], owner_telegram_ids=[])
    apply_filter_command(uf, "owner @tgmefchik, 8556771897")
    assert (uf.owner_usernames, uf.owner_telegram_ids) == (["tgmefchik"], [8556771897])
    for bad in ("owner @ab", "owner <b>", "owner @x!y"):
        with pytest.raises(FilterInputError):
            apply_filter_command(uf, bad)
    apply_filter_command(uf, "owner")
    assert uf.owner_usernames == [] and uf.owner_telegram_ids == []


# --- end to end -------------------------------------------------------------------------------

async def add_user(sessions, tid, **kw):
    async with sessions.begin() as s:
        s.add(User(telegram_id=tid, filters=[UserFilter(**kw)]))


async def notified(sessions):
    async with sessions() as s:
        return sorted((await s.execute(select(User.telegram_id, Notification.event_key).join(User))).all())


async def listing_key(sessions, slug=SLUG):
    async with sessions() as s:
        lid = (await s.execute(select(Listing.id).join(Gift).where(Gift.slug == slug, Listing.is_active.is_(True)))).scalar_one()
    return f"NEW_LISTING:{lid}"


async def drain(pipe):
    while await pipe.process_one():
        pass


async def test_owner_value_filter_checks_owner_of_scanned_nfts(sessions):
    mock = MockProvider({SLUG: owner(OwnerStatus.FOUND, 8556771897, "tgmefchik")},
                        default=OwnerStatus.NOT_FOUND)
    pipe, scanner = build_pipeline_with_page(sessions, mock)
    await add_user(sessions, 1, collections=["IonGem"], owner_usernames=["tgmefchik"])
    await add_user(sessions, 2, collections=["IonGem"], owner_usernames=["someone_else"])
    await scanner.run_cycle()
    await drain(pipe)
    key = await listing_key(sessions)
    got = await notified(sessions)
    assert got == [(1, key)]  # only the NFT whose owner is @tgmefchik, only to user 1
    # Kartoshka received NFT identifiers only: never a username or an ID.
    assert mock.calls and all(c.count("-") >= 1 and c.rsplit("-", 1)[1].isdigit() for c in mock.calls)
    assert "tgmefchik" not in mock.calls and "someone_else" not in mock.calls
    assert len(mock.calls) == len(set(mock.calls))  # each NFT asked once


async def test_seller_change_refreshes_owner_instead_of_stale_cache(sessions):
    mock = MockProvider({SLUG: owner(OwnerStatus.FOUND, 1, "first_owner")})
    pipe, scanner = build_pipeline_with_page(sessions, mock)
    await add_user(sessions, 1, collections=["IonGem"])
    await scanner.run_cycle()
    await drain(pipe)
    assert mock.calls.count(SLUG) == 1
    sold = copy.deepcopy(scanner._client.page)
    next(g for g in sold["gifts"] if g["slug"] == SLUG)["owner_name"] = "New Seller"
    scanner._client.page = sold
    mock.set(SLUG, owner(OwnerStatus.FOUND, 2, "second_owner"))
    await scanner.run_cycle()
    await drain(pipe)
    assert mock.calls.count(SLUG) == 2  # cache invalidated by the new episode
    async with sessions() as s:
        listings = (await s.execute(select(Listing).join(Gift).where(Gift.slug == SLUG))).scalars().all()
    assert sorted(l.listing_identity for l in listings) == [f"{SLUG}:1", f"{SLUG}:2"]


async def test_same_owner_relist_within_ttl_uses_cache(sessions):
    mock = MockProvider({SLUG: owner(OwnerStatus.FOUND, 1, "u")})
    pipe, scanner = build_pipeline_with_page(sessions, mock)
    await add_user(sessions, 1, collections=["IonGem"])
    await scanner.run_cycle()
    await drain(pipe)
    async with sessions.begin() as s:  # listing closed (e.g. full scan saw it gone), seller unchanged
        l = (await s.execute(select(Listing).join(Gift).where(Gift.slug == SLUG))).scalar_one()
        l.is_active, l.state = False, "INACTIVE"
    await scanner.run_cycle()  # back on sale -> episode 2, same seller
    await drain(pipe)
    assert mock.calls.count(SLUG) == 1


async def test_paid_but_unusable_answer_is_not_bought_again(sessions):
    async with sessions.begin() as s:
        g = Gift(telegram_unique_id=1, gift_type_id=2, slug=SLUG, collection="IonGem", number=3185, raw_data={})
        s.add(g)
    paid_error = OwnerData(OwnerStatus.ERROR, "mock", NOW, http_status=200, reason="malformed")
    mock = MockProvider({SLUG: paid_error})
    clock = type("C", (), {"now": NOW, "__call__": lambda self: self.now})()
    r = OwnerResolver(sessions, mock, None, cache_ttl_s=86400, clock=clock)
    await r.resolve(g.id)
    await r.resolve(g.id)
    assert len(mock.calls) == 1
    clock.now = NOW + timedelta(hours=1, seconds=1)
    await r.resolve(g.id)
    assert len(mock.calls) == 2


@pytest.mark.parametrize("answer,status,owner_rows", [
    (owner(OwnerStatus.FOUND, 7, "seller"), "FOUND", 1),
    (owner(OwnerStatus.FOUND, 7, None), "NO_USERNAME", 0),  # ID only: cached, no owner record
    (owner(OwnerStatus.HIDDEN), "HIDDEN", 0),
    (owner(OwnerStatus.NOT_FOUND), "NOT_FOUND", 0),
])
async def test_owner_records_only_with_username_and_no_repeat_payment(sessions, answer, status, owner_rows):
    from app.database.models import Owner, OwnerLookupCache
    async with sessions.begin() as s:
        g = Gift(telegram_unique_id=1, gift_type_id=2, slug=SLUG, collection="IonGem", number=3185, raw_data={})
        s.add(g)
    mock = MockProvider({SLUG: answer})
    r = OwnerResolver(sessions, mock, None, cache_ttl_s=86400)
    first, second = await r.resolve(g.id), await r.resolve(g.id)
    assert len(mock.calls) == 1 and second.source == "cache"
    assert first.data.status.value == status and first.data.telegram_id == (7 if status == "FOUND" else None)
    async with sessions() as s:
        assert len((await s.execute(select(Owner))).scalars().all()) == owner_rows
        row = (await s.execute(select(OwnerLookupCache))).scalar_one()
    assert row.status == status and row.owner_id is (None if owner_rows == 0 else row.owner_id)


async def test_hidden_owner_lookup_is_logged(sessions, caplog):
    async with sessions.begin() as s:
        g = Gift(telegram_unique_id=1, gift_type_id=2, slug=SLUG, collection="IonGem", number=3185, raw_data={})
        s.add(g)
    r = OwnerResolver(sessions, MockProvider(default=OwnerStatus.HIDDEN), None, cache_ttl_s=60)
    with caplog.at_level("INFO", logger="app.api"):
        await r.resolve(g.id)
    assert any(SLUG in m and "скрыл профиль" in m for m in (x.getMessage() for x in caplog.records))


async def test_unpaid_failures_are_retried(sessions):
    async with sessions.begin() as s:
        g = Gift(telegram_unique_id=1, gift_type_id=2, slug=SLUG, collection="IonGem", number=3185, raw_data={})
        s.add(g)
    mock = MockProvider({SLUG: OwnerData(OwnerStatus.UNAVAILABLE, "mock", NOW, http_status=429, reason="rate_limited")})
    r = OwnerResolver(sessions, mock, None, cache_ttl_s=86400)
    await r.resolve(g.id)
    await r.resolve(g.id)
    assert len(mock.calls) == 2
