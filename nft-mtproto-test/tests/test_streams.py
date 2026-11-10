"""Three independent streams: NFT notifications -> subscriber DMs, service logs -> LOG_GROUP_ID only,
local file logs -> logs/app.log. They never cross, also when a send fails."""
import logging
from decimal import Decimal

from app.database.types import utcnow
from app.providers.base import OwnerData, OwnerStatus
from app.services.api_usage import ApiUsageManager
from app.services.log_sender import TelegramLogHandler, TelegramLogSender
from app.services.notifications import NotificationSender
from app.utils.log import setup_logging
from tests.test_bot import note, seed_notification
from tests.test_log_sender import GROUP, FakeTelegram, root_logger, running, settle  # noqa: F401

SUBSCRIBER = 100  # telegram_id created by seed_notification
CARD_HEADER = "Новый листинг"


class UserTelegram:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[tuple[int, str]] = []

    async def send(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))
        if self.fail:
            raise RuntimeError("user delivery failed")


def log_stream(root, raw=str(GROUP), fail=False):
    group = FakeTelegram(fail=fail)
    sender = TelegramLogSender(raw, group.send, group.get_chat)
    root.addHandler(TelegramLogHandler(sender))
    return group, sender


async def test_nft_notification_goes_to_the_subscriber(sessions, root_logger):
    group, sender = log_stream(root_logger)
    stop, task = await running(sender)
    nid = await seed_notification(sessions)
    users = UserTelegram()
    assert await NotificationSender(sessions, users.send).send_pending() == 1
    await settle(sender, stop, task)
    assert [c for c, _ in users.sent] == [SUBSCRIBER] and CARD_HEADER in users.sent[0][1]
    assert (await note(sessions, nid)).status == "SENT"
    assert not any(CARD_HEADER in t for _, t in group.sent)  # the card never reaches the log group


async def test_service_log_goes_only_to_log_group(sessions, root_logger):
    group, sender = log_stream(root_logger)
    stop, task = await running(sender)
    users = UserTelegram()
    usage = ApiUsageManager(sessions, "kartoshka", Decimal("1"), None)  # no admin DM channel any more
    await usage.record(OwnerData(OwnerStatus.UNAVAILABLE, "kartoshka", utcnow(), http_status=402,
                                 reason="payment_required"))
    await settle(sender, stop, task)
    assert group.sent and {c for c, _ in group.sent} == {GROUP}
    assert any("402" in t for _, t in group.sent)
    assert users.sent == []  # no subscriber or admin received the service event


async def test_streams_do_not_cross_when_nft_delivery_fails(sessions, root_logger):
    group, sender = log_stream(root_logger)
    stop, task = await running(sender)
    nid = await seed_notification(sessions)
    users = UserTelegram(fail=True)
    await NotificationSender(sessions, users.send).send_pending()
    await settle(sender, stop, task)
    assert {c for c, _ in users.sent} == {SUBSCRIBER}  # retried to the subscriber, never to the group
    assert (await note(sessions, nid)).status == "PENDING"  # will be retried by the Notification Service
    assert {c for c, _ in group.sent} <= {GROUP}
    assert not any(CARD_HEADER in t for _, t in group.sent)  # the failure is reported, the card is not copied


async def test_streams_do_not_cross_when_log_group_fails(sessions, root_logger):
    group, sender = log_stream(root_logger, fail=True)
    stop, task = await running(sender)
    logging.getLogger("app.mtproto").error("scan cycle stopped")
    nid = await seed_notification(sessions)
    users = UserTelegram()
    assert await NotificationSender(sessions, users.send).send_pending() == 1
    await settle(sender, stop, task)
    assert users.sent[0][0] == SUBSCRIBER and (await note(sessions, nid)).status == "SENT"
    assert {c for c, _ in group.sent} == {GROUP} and not any(CARD_HEADER in t for _, t in group.sent)


async def test_nft_notifications_work_without_log_group(sessions, root_logger):
    group, sender = log_stream(root_logger, raw="")
    stop, task = await running(sender)  # disabled: validate() fails
    nid = await seed_notification(sessions)
    users = UserTelegram()
    assert await NotificationSender(sessions, users.send).send_pending() == 1
    await settle(sender, stop, task)
    assert users.sent[0][0] == SUBSCRIBER and (await note(sessions, nid)).status == "SENT"
    assert group.sent == []


async def test_both_failing_still_isolated_and_recorded_locally(sessions, root_logger, tmp_path):
    setup_logging("INFO", tmp_path)
    group, sender = log_stream(logging.getLogger(), fail=True)
    stop, task = await running(sender)
    await seed_notification(sessions)
    users = UserTelegram(fail=True)
    await NotificationSender(sessions, users.send).send_pending()
    await settle(sender, stop, task)
    assert {c for c, _ in users.sent} == {SUBSCRIBER} and {c for c, _ in group.sent} <= {GROUP}
    for h in logging.getLogger().handlers:
        h.flush()
    local = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "attempt failed" in local  # stream 3 has the NFT delivery failure
    assert "sending a log to LOG_GROUP_ID failed" in local  # and the log group failure
    setup_logging("INFO")
