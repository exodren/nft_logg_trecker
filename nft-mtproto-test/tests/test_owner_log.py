"""Owner log group: queued only for matched listings with a public owner; first claim wins."""
from decimal import Decimal
from types import SimpleNamespace

from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select

from app.database.models import Notification, OwnerLogPost, UserFilter
from app.providers.base import OwnerStatus
from app.providers.mock import MockProvider
from app.services import owner_log
from app.services.owner_log import OwnerLogSender, claim
from tests.test_pipeline import SLUG, Clock, FakeTelegram, add_user, drain, owner, page
from app.mtproto.scanner import ListingScanner
from app.services.enrichment import OwnerResolver
from app.services.pipeline import Pipeline
from app.services.queue import TaskQueue

GROUP = -100123


class FakeBot:
    def __init__(self, blocked=()):
        self.sent, self.edited, self.blocked = [], [], set(blocked)

    async def send_message(self, chat_id, text, **kw):
        if chat_id in self.blocked:
            raise TelegramForbiddenError(method=None, message="blocked")
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent))

    async def edit_message_text(self, text, chat_id, message_id, reply_markup=None):
        self.edited.append((chat_id, message_id, text))


async def run(sessions, status, username):
    clock = Clock()
    queue = TaskQueue(sessions, clock=clock)
    mock = MockProvider({SLUG: owner(status, 8556771897, username)})
    pipe = Pipeline(sessions, queue, OwnerResolver(sessions, mock, None, cache_ttl_s=3600, clock=clock),
                    clock=clock, owner_log=True)
    await add_user(sessions, 1, collections=["IonGem"], price_max=Decimal(8755), notifications_enabled=True)
    await ListingScanner(FakeTelegram(page()), sessions, 60, on_events=pipe.on_events, clock=clock).run_cycle()
    await drain(pipe)
    async with sessions() as s:
        return (await s.execute(select(OwnerLogPost))).scalars().all()


async def test_hidden_or_no_username_owner_is_not_posted(sessions):
    assert await run(sessions, OwnerStatus.HIDDEN, None) == []


async def test_found_owner_posted_without_owner_then_first_claim_wins(sessions, monkeypatch):
    monkeypatch.setattr(owner_log, "GROUP_GAP_S", 0)
    [post] = await run(sessions, OwnerStatus.FOUND, "tgmefchik")
    async with sessions() as s:
        assert (await s.execute(select(Notification))).first() is None  # group only, no DM
    bot = FakeBot(blocked={777})
    assert await OwnerLogSender(sessions, bot, GROUP).post_one()
    chat, text = bot.sent[-1]
    assert chat == GROUP and "tgmefchik" not in text  # owner hidden until claimed

    assert "/start" in await claim(sessions, bot, GROUP, post.id, 777)  # cannot DM: log stays free
    assert await claim(sessions, bot, GROUP, post.id, 42) == "Лог отправлен вам в личку"
    assert bot.sent[-1][0] == 42 and "@tgmefchik" in bot.sent[-1][1]
    assert bot.edited == [(GROUP, 1, owner_log.CLAIMED_TEXT)]
    assert await claim(sessions, bot, GROUP, post.id, 43) == "Лог уже занят"


async def test_backfill_in_group_mode_posts_to_group(sessions):
    clock = Clock()
    queue = TaskQueue(sessions, clock=clock)
    mock = MockProvider({SLUG: owner(OwnerStatus.FOUND, 8556771897, "tgmefchik")})
    pipe = Pipeline(sessions, queue, OwnerResolver(sessions, mock, None, cache_ttl_s=3600, clock=clock),
                    clock=clock, owner_log=True)
    uid = await add_user(sessions, 1, collections=["IonGem"], price_max=Decimal(8755), notifications_enabled=False)
    await ListingScanner(FakeTelegram(page()), sessions, 60, on_events=pipe.on_events, clock=clock).run_cycle()
    async with sessions.begin() as s:
        f = (await s.execute(select(UserFilter).where(UserFilter.user_id == uid))).scalar_one()
        f.notifications_enabled = True
    assert await pipe.backfill(f.id) > 0
    await drain(pipe)
    async with sessions() as s:
        assert (await s.execute(select(OwnerLogPost))).scalars().all()
        assert (await s.execute(select(Notification))).first() is None
