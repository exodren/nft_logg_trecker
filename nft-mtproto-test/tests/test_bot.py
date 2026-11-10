"""Bot without Telegram: formatters, search parsing, filter input, dispatcher with a fake API session,
and the notification sender with fake send functions."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, TelegramMethod
from aiogram.types import Chat, Message, Update, User as TgUser
from sqlalchemy import select

from app.bot.factory import BotDeps, create_dispatcher
from app.bot.formatters import SOURCE_LINE, card_text, money, nft_url
from app.bot.handlers import FilterInputError, apply_filter_command
from app.config import load_settings
from app.database.models import Gift, Listing, Notification, OwnerLookupCache, User, UserFilter
from app.providers.base import OwnerData, OwnerStatus
from app.services.notifications import NotificationSender
from app.services.queries import Card, parse_query
from app.services.queue import TaskQueue

NOW = datetime(2026, 11, 10, 21, 30, tzinfo=timezone.utc)
ADMIN = 777


def gift(**kw):
    d = dict(id=1, telegram_unique_id=1, gift_type_id=2, slug="MoonPendant-71710", collection="MoonPendant",
             number=71710, title="Moon Pendant", model="Golden Moon", backdrop="Cyberpunk", pattern="Mountain Lion",
             raw_data={})
    return Gift(**{**d, **kw})


def listing(**kw):
    d = dict(id=1, gift_id=1, listing_identity="MoonPendant-71710:1", episode=1, price=Decimal(800), currency="XTR",
             is_active=True, listing_date=NOW, first_seen_at=NOW, last_seen_at=NOW, raw_data={},
             owner_lookup_status="DONE")
    return Listing(**{**d, **kw})


def od(status, **kw):
    return OwnerData(status=status, provider="kartoshka", fetched_at=NOW, **kw)


# --- formatter --------------------------------------------------------------------------------------

def test_card_found_owner_shows_username_id_and_source():
    text = card_text(Card(gift(), listing(), od(OwnerStatus.FOUND, telegram_id=8556771897, username="tgmefchik"), "DONE"))
    assert "Moon Pendant #71710" in text and "800 ⭐" in text
    assert "@tgmefchik" in text and "8556771897" in text and SOURCE_LINE in text
    assert 'href="https://t.me/nft/MoonPendant-71710"' in text


@pytest.mark.parametrize("o", [od(OwnerStatus.FOUND, telegram_id=5), od(OwnerStatus.NO_USERNAME),
                               od(OwnerStatus.NOT_FOUND), od(OwnerStatus.UNAVAILABLE)])
def test_card_shows_owner_block_only_with_username(o):
    text = card_text(Card(gift(), listing(), o, "DONE"))
    assert "@" not in text and "Telegram ID" not in text and "Владелец" not in text and SOURCE_LINE not in text


def test_hidden_owner_is_not_sent_to_telegram():
    c = Card(gift(), listing(), od(OwnerStatus.HIDDEN, is_hidden=True), "DONE")
    for text in (card_text(c), __import__("app.bot.formatters", fromlist=["x"]).short_line(c)):
        assert "скрыл профиль" not in text and "🔒" not in text and "Владелец" not in text


def test_card_unknown_price_and_no_lookup():
    text = card_text(Card(gift(), listing(price=None, currency=None, owner_lookup_status="NOT_NEEDED"), None, "NOT_NEEDED"))
    assert "Цена не определена" in text and "Владелец" not in text and SOURCE_LINE not in text


def test_card_escapes_html_and_rejects_bad_slug_links():
    text = card_text(Card(gift(model="<b>x</b>", slug="evil\"><script>", title="A&B"), listing(), None, None))
    assert "<b>x</b>" not in text and "&lt;b&gt;" in text and "A&amp;B" in text and "Открыть NFT" not in text
    assert nft_url("IonGem-3185") and nft_url("javascript:alert(1)") is None


def test_money_formats():
    assert money(Decimal(8755), "XTR") == "8 755 ⭐"
    assert money(Decimal("123.240000000"), "TON") == "123.24 TON"
    assert money(None, None) == "Цена не определена"


@pytest.mark.parametrize("q,expected", [
    ("MoonPendant-71710", ("slug", "MoonPendant", 71710)),
    ("Moon Pendant 71710", ("slug", "Moon Pendant", 71710)),
    ("IonGem-2558", ("slug", "IonGem", 2558)),
    ("IonGem", ("collection", "IonGem", None)),
    ("71710", ("number", None, 71710)),
    ("'; DROP TABLE gifts;--", ("invalid", None, None)),
    ("x" * 500, ("invalid", None, None)),
])
def test_parse_query(q, expected):
    assert parse_query(q) == expected


def test_filter_command_parsing():
    f = UserFilter(collections=[], models=[], backdrops=[], patterns=[], price_currency="XTR")
    apply_filter_command(f, "collections IonGem, Moon Pendant")
    apply_filter_command(f, "price 100 - TON")
    assert f.collections == ["IonGem", "Moon Pendant"] and (f.price_min, f.price_max, f.price_currency) == (Decimal(100), None, "TON")
    for bad in ("price 5 1", "price abc 2", "models <script>", "unknown x", "price -5 10"):
        with pytest.raises(FilterInputError):
            apply_filter_command(f, bad)
    apply_filter_command(f, "reset")
    assert f.collections == [] and f.price_min is None


# --- dispatcher with a fake Telegram API ---------------------------------------------------------

class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent: list[TelegramMethod] = []

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        self.sent.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(message_id=1, date=NOW, chat=Chat(id=1, type="private"), text=method.text)
        if isinstance(method, AnswerCallbackQuery):
            return True
        return True

    async def stream_content(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError

    async def close(self):
        pass


def update(text: str, user_id: int = 1, uid: int = 1) -> Update:
    return Update(update_id=uid, message=Message(
        message_id=uid, date=NOW, chat=Chat(id=user_id, type="private"),
        from_user=TgUser(id=user_id, is_bot=False, first_name="Ann", username="ann"), text=text))


@pytest.fixture
async def bot_env(sessions):
    session = FakeSession()
    bot = Bot("42:" + "A" * 35, session=session)
    deps = BotDeps(settings=load_settings({"ADMIN_TELEGRAM_IDS": str(ADMIN)}), sessions=sessions,
                   queue=TaskQueue(sessions))
    dp = create_dispatcher(deps)
    return dp, bot, session


async def test_start_registers_user_and_shows_menu(bot_env, sessions):
    dp, bot, session = bot_env
    await dp.feed_update(bot, update("/start"))
    assert "Выберите действие" in session.sent[-1].text and session.sent[-1].reply_markup is not None
    async with sessions() as s:
        u = (await s.execute(select(User))).scalar_one()
    assert (u.telegram_id, u.username, u.first_name) == (1, "ann", "Ann")


async def test_search_finds_saved_nft_without_api(bot_env, sessions):
    dp, bot, session = bot_env
    async with sessions.begin() as s:
        g = gift(id=None)
        s.add(g)
        await s.flush()
        s.add(listing(id=None, gift_id=g.id))
    await dp.feed_update(bot, update("/search Moon Pendant 71710"))
    assert "Moon Pendant #71710" in session.sent[-1].text
    await dp.feed_update(bot, update("/search Nope-1", user_id=2, uid=2))  # other user: no throttling
    assert "не найден" in session.sent[-1].text


async def test_stats_and_settings_persist(bot_env, sessions):
    dp, bot, session = bot_env
    await dp.feed_update(bot, update("/stats"))
    assert "NFT в базе: 0" in session.sent[-1].text
    await dp.feed_update(bot, update("/filter collections IonGem", user_id=2, uid=2))
    assert "Фильтр сохранён" in session.sent[-1].text
    async with sessions() as s:
        f = (await s.execute(select(UserFilter))).scalar_one()
    assert f.collections == ["IonGem"] and f.notifications_enabled is False  # opt-in


async def test_admin_commands_are_hidden_from_users(bot_env):
    dp, bot, session = bot_env
    await dp.feed_update(bot, update("/admin", user_id=1))
    assert session.sent == []  # silently ignored
    await dp.feed_update(bot, update("/admin", user_id=ADMIN, uid=2))
    assert "Админ-панель" in session.sent[-1].text


async def test_rapid_commands_are_throttled(bot_env):
    dp, bot, session = bot_env
    await dp.feed_update(bot, update("/help", uid=1))
    await dp.feed_update(bot, update("/help", uid=2))
    assert len(session.sent) == 1


# --- notification sender ----------------------------------------------------------------------------

async def seed_notification(sessions, event="NEW_LISTING"):
    async with sessions.begin() as s:
        u = User(telegram_id=100)
        g = gift(id=None)
        s.add_all([u, g])
        await s.flush()
        l = listing(id=None, gift_id=g.id)
        s.add(l)
        await s.flush()
        s.add(OwnerLookupCache(gift_id=g.id, status="HIDDEN", is_hidden=True, provider="kartoshka", fetched_at=NOW,
                               expires_at=NOW, last_checked_at=NOW))
        n = Notification(user_id=u.id, listing_id=l.id, event_type=event, event_key=f"{event}:{l.id}", status="PENDING")
        s.add(n)
    return n.id


async def note(sessions, nid):
    async with sessions() as s:
        return await s.get(Notification, nid)


async def test_sender_sends_card_once_and_logs_hidden_owner(sessions, caplog):
    nid = await seed_notification(sessions)  # owner is HIDDEN
    sent = []

    async def send(chat_id, text, reply_markup=None):
        sent.append((chat_id, text))
    sender = NotificationSender(sessions, send)
    with caplog.at_level("INFO", logger="app.bot"):
        assert await sender.send_pending() == 1
    assert await sender.send_pending() == 0  # no duplicates
    n = await note(sessions, nid)
    assert n.status == "SENT" and n.sent_at is not None
    assert sent[0][0] == 100 and "Новый листинг" in sent[0][1] and "скрыл профиль" not in sent[0][1]
    assert any("скрыл профиль" in r.getMessage() and "MoonPendant-71710" in r.getMessage() for r in caplog.records)


async def test_sender_retry_after_keeps_pending(sessions):
    nid = await seed_notification(sessions)
    waits = []

    async def send(chat_id, text, reply_markup=None):
        raise TelegramRetryAfter(method=SendMessage(chat_id=1, text="x"), message="flood", retry_after=3)

    async def sleep(s):
        waits.append(s)
    await NotificationSender(sessions, send, sleep=sleep).send_pending()
    assert (await note(sessions, nid)).status == "PENDING" and waits == [3]


async def test_sender_blocked_user_marks_failed_and_inactive(sessions):
    nid = await seed_notification(sessions)

    async def send(chat_id, text, reply_markup=None):
        raise TelegramForbiddenError(method=SendMessage(chat_id=1, text="x"), message="blocked")
    await NotificationSender(sessions, send).send_pending()
    n = await note(sessions, nid)
    async with sessions() as s:
        u = (await s.execute(select(User))).scalar_one()
    assert n.status == "FAILED" and not u.is_active


async def test_sender_generic_errors_fail_after_three_attempts(sessions):
    nid = await seed_notification(sessions)

    async def send(chat_id, text, reply_markup=None):
        raise RuntimeError("network")
    sender = NotificationSender(sessions, send)
    for _ in range(3):
        await sender.send_pending()
    n = await note(sessions, nid)
    assert n.status == "FAILED" and n.attempts == 3


def test_notification_service_has_no_admin_channel():
    """Service events are not sent by the Notification Service (they go to LOG_GROUP_ID via the log sender)."""
    assert not hasattr(NotificationSender, "notify_admins")
