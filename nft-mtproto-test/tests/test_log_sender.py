"""Telegram Log Sender: only LOG_GROUP_ID ever receives service logs."""
import asyncio
import inspect
import logging
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.providers.base import HIDDEN_OWNER_TEXT
from app.services.log_sender import RecipientNotAllowed, TelegramLogHandler, TelegramLogSender
from app.utils.log import setup_logging

GROUP = -1001234567890
TOKEN_IN_LOG = "123456789:AAH_example-token-value-xyz"


class FakeTelegram:
    def __init__(self, chat_type="supergroup", fail=False):
        self.chat_type = chat_type
        self.fail = fail
        self.sent: list[tuple[int, str]] = []
        self.get_chat_calls: list[int] = []

    async def send(self, chat_id, text):
        self.sent.append((chat_id, text))
        if self.fail:
            raise ConnectionError("telegram down")

    async def get_chat(self, chat_id):
        self.get_chat_calls.append(chat_id)
        return SimpleNamespace(id=chat_id, type=self.chat_type)


@pytest.fixture
def root_logger():
    root = logging.getLogger()
    before = list(root.handlers)
    root.setLevel(logging.DEBUG)
    yield root
    for h in list(root.handlers):
        if h not in before:
            root.removeHandler(h)


async def running(sender: TelegramLogSender):
    stop = asyncio.Event()
    task = asyncio.create_task(sender.run(stop))
    for _ in range(100):
        if sender._loop is not None or task.done():
            break
        await asyncio.sleep(0.01)
    return stop, task


async def settle(sender, stop, task):
    for _ in range(100):
        await asyncio.sleep(0.01)
        if sender._queue.empty():
            break
    await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, 2)


async def test_all_service_logs_go_only_to_log_group(root_logger):
    tg = FakeTelegram()
    sender = TelegramLogSender(str(GROUP), tg.send, tg.get_chat)
    root_logger.addHandler(TelegramLogHandler(sender))
    stop, task = await running(sender)
    logging.getLogger("app.mtproto").warning("FloodWait 30s")
    logging.getLogger("app.api").error("kartoshka: 402")
    logging.getLogger("app.database").critical("db locked")
    await settle(sender, stop, task)
    assert [c for c, _ in tg.sent] == [GROUP, GROUP, GROUP]
    assert sender.allowed_chat_id == GROUP


async def test_incoming_chat_id_cannot_change_recipient(root_logger):
    tg = FakeTelegram()
    sender = TelegramLogSender(str(GROUP), tg.send, tg.get_chat)
    root_logger.addHandler(TelegramLogHandler(sender))
    assert list(inspect.signature(sender.send_log).parameters) == ["text"]  # no way to pass a recipient
    stop, task = await running(sender)
    logging.getLogger("app.bot").warning("update from chat %s", 555, extra={"chat_id": 555, "chat": {"id": 777}})
    await sender.send_log("message mentioning chat_id=999")
    await settle(sender, stop, task)
    assert {c for c, _ in tg.sent} == {GROUP} and len(tg.sent) == 2


async def test_sending_to_another_chat_is_blocked():
    tg = FakeTelegram()
    sender = TelegramLogSender(str(GROUP), tg.send, tg.get_chat)
    assert await sender.validate()
    with pytest.raises(RecipientNotAllowed):
        sender._guard(999)
    assert await sender._deliver(999, "x") is False and await sender._deliver(-100999, "x") is False
    assert tg.sent == []


@pytest.mark.parametrize("raw", [None, "", "   "])
async def test_empty_log_group_never_sends(raw, tmp_path):
    setup_logging("INFO", tmp_path)
    tg = FakeTelegram()
    sender = TelegramLogSender(raw, tg.send, tg.get_chat)
    assert await sender.validate() is False
    assert await sender.send_log("x") is False and tg.sent == [] and tg.get_chat_calls == []
    logging.getLogger().handlers[-1].flush()
    assert "LOG_GROUP_ID is not set" in (tmp_path / "app.log").read_text(encoding="utf-8")
    setup_logging("INFO")


@pytest.mark.parametrize("raw", ["abc", "123456", "-12a", "1e9", "@group", "-"])
async def test_invalid_log_group_never_sends(raw):
    tg = FakeTelegram()
    sender = TelegramLogSender(raw, tg.send, tg.get_chat)
    assert await sender.validate() is False
    assert await sender.send_log("x") is False and tg.sent == [] and tg.get_chat_calls == []


@pytest.mark.parametrize("chat_type", ["private", "channel", ""])
async def test_private_chat_and_channel_are_rejected(chat_type):
    tg = FakeTelegram(chat_type=chat_type)
    sender = TelegramLogSender(str(GROUP), tg.send, tg.get_chat)
    assert await sender.validate() is False
    assert await sender.send_log("x") is False and tg.sent == []


async def test_group_and_supergroup_are_accepted():
    for t in ("group", "supergroup"):
        assert await TelegramLogSender(str(GROUP), FakeTelegram(t).send, FakeTelegram(t).get_chat).validate()


async def test_getchat_failure_disables_sender():
    async def broken(chat_id):
        raise ConnectionError("no access")
    sender = TelegramLogSender(str(GROUP), FakeTelegram().send, broken)
    assert await sender.validate() is False


async def test_send_failure_is_logged_locally_without_recursion(root_logger, tmp_path):
    setup_logging("INFO", tmp_path)
    tg = FakeTelegram(fail=True)
    sender = TelegramLogSender(str(GROUP), tg.send, tg.get_chat)
    root_logger.addHandler(TelegramLogHandler(sender))
    stop, task = await running(sender)
    logging.getLogger("app.api").error("first error")
    await settle(sender, stop, task)
    assert len(tg.sent) == 1  # the failure did not produce a second Telegram attempt
    for h in logging.getLogger().handlers:
        h.flush()
    content = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "sending a log to LOG_GROUP_ID failed: ConnectionError" in content
    setup_logging("INFO")


async def test_aiogram_and_sender_logs_are_not_forwarded(root_logger):
    tg = FakeTelegram()
    sender = TelegramLogSender(str(GROUP), tg.send, tg.get_chat)
    root_logger.addHandler(TelegramLogHandler(sender))
    stop, task = await running(sender)
    logging.getLogger("aiogram.event").error("Failed to send")
    logging.getLogger("app.logsender").error("sending a log failed")
    await settle(sender, stop, task)
    assert tg.sent == []


async def test_debug_and_info_are_not_forwarded_and_duplicates_suppressed(root_logger):
    tg = FakeTelegram()
    sender = TelegramLogSender(str(GROUP), tg.send, tg.get_chat)
    root_logger.addHandler(TelegramLogHandler(sender))
    stop, task = await running(sender)
    logging.getLogger("app.api").debug("debug details")
    logging.getLogger("app.api").info("owner lookup ok")
    for _ in range(5):
        logging.getLogger("app.api").warning("same warning")
    await settle(sender, stop, task)
    assert len(tg.sent) == 1 and "same warning" in tg.sent[0][1]


async def test_secrets_are_masked_in_telegram_logs(root_logger):
    tg = FakeTelegram()
    sender = TelegramLogSender(str(GROUP), tg.send, tg.get_chat)
    root_logger.addHandler(TelegramLogHandler(sender, secrets=["kartoshka-secret"]))
    stop, task = await running(sender)
    logging.getLogger("app.api").error("token %s and kartoshka-secret failed", TOKEN_IN_LOG)
    await settle(sender, stop, task)
    text = tg.sent[0][1]
    assert TOKEN_IN_LOG not in text and "kartoshka-secret" not in text and "***" in text


async def test_hidden_owner_never_reaches_telegram_logs(root_logger):
    tg = FakeTelegram()
    sender = TelegramLogSender(str(GROUP), tg.send, tg.get_chat)
    root_logger.addHandler(TelegramLogHandler(sender))
    stop, task = await running(sender)
    logging.getLogger("app.api").info("IonGem-3185: %s", HIDDEN_OWNER_TEXT)  # INFO, as the app logs it
    logging.getLogger("app.bot").warning("IonGem-3185: %s", HIDDEN_OWNER_TEXT)  # even at WARNING
    await settle(sender, stop, task)
    assert tg.sent == []


async def test_user_notifications_are_independent_of_log_group(sessions, root_logger):
    from tests.test_bot import note, seed_notification
    from app.services.notifications import NotificationSender
    tg = FakeTelegram()
    log_sender = TelegramLogSender(str(GROUP), tg.send, tg.get_chat)
    root_logger.addHandler(TelegramLogHandler(log_sender))
    stop, task = await running(log_sender)
    nid = await seed_notification(sessions)
    user_sent = []

    async def user_send(chat_id, text, reply_markup=None):
        user_sent.append(chat_id)
    assert await NotificationSender(sessions, user_send).send_pending() == 1
    await settle(log_sender, stop, task)
    assert user_sent == [100] and (await note(sessions, nid)).status == "SENT"
    assert all(c == GROUP for c, _ in tg.sent)
    assert not any("Новый листинг" in t for _, t in tg.sent)  # user cards never go to the log group


async def test_end_to_end_with_aiogram_bot_on_local_api_server(root_logger):
    """Real aiogram Bot against a local HTTP server that imitates the Bot API."""
    calls = []

    async def method(request: web.Request):
        data = dict(await request.post()) or (await request.json() if request.can_read_body else {})
        name = request.match_info["method"]
        calls.append((name, data))
        if name == "getChat":
            return web.json_response({"ok": True, "result": {"id": int(data["chat_id"]), "type": "supergroup",
                                                             "title": "logs", "accent_color_id": 0,
                                                             "max_reaction_count": 0, "accepted_gift_types": {
                                                                 "unlimited_gifts": False, "limited_gifts": False,
                                                                 "unique_gifts": False, "premium_subscription": False,
                                                                 "gifts_from_channels": False}}})
        return web.json_response({"ok": True, "result": {"message_id": 1, "date": 0, "text": data.get("text", ""),
                                                         "chat": {"id": int(data["chat_id"]), "type": "supergroup"}}})
    app = web.Application()
    app.router.add_post("/bot{token}/{method}", method)
    srv = TestServer(app)
    await srv.start_server()
    bot = Bot("42:" + "A" * 35, session=AiohttpSession(api=TelegramAPIServer.from_base(str(srv.make_url("")))))
    try:
        sender = TelegramLogSender(str(GROUP), lambda c, t: bot.send_message(c, t, parse_mode=None), bot.get_chat)
        root_logger.addHandler(TelegramLogHandler(sender))
        stop, task = await running(sender)
        logging.getLogger("app.mtproto").error("scan cycle stopped")
        await settle(sender, stop, task)
    finally:
        await bot.session.close()
        await srv.close()
    sends = [d for n, d in calls if n == "sendMessage"]
    assert [n for n, _ in calls][0] == "getChat"
    assert len(sends) == 1 and int(sends[0]["chat_id"]) == GROUP and "scan cycle stopped" in sends[0]["text"]
