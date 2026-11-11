"""Single entry point: python -m app. Scanner, workers, notifications, bot and backups run as independent
asyncio tasks; stopping one never stops the others."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from contextlib import suppress

from app import __version__
from app.bot.factory import BotDeps, create_bot, create_dispatcher
from app.config import ROOT_DIR, ConfigError, Settings, load_settings
from app.database.session import create_engine, migrate, session_factory
from app.mtproto.client import MTProtoClient, MTProtoError, NotAuthorized
from app.mtproto.scanner import ListingScanner
from app.providers.kartoshka import KartoshkaApiProvider
from app.services.api_usage import ApiUsageManager
from app.services.backup import run_backups
from app.services.enrichment import OwnerResolver
from app.services.log_sender import TelegramLogHandler, TelegramLogSender
from app.services.notifications import NotificationSender
from app.services.pipeline import Pipeline
from app.services.owner_log import OwnerLogSender
from app.services.queue import TaskQueue
from app.supervisor import FatalComponentError, Supervisor
from app.utils.log import setup_logging

log = logging.getLogger("app")


async def run(settings: Settings) -> None:
    setup_logging(settings.log_level, ROOT_DIR / "logs", settings.secrets())
    log.info("NFT Tracker %s starting", __version__)
    await migrate(settings.database_url)
    engine = create_engine(settings.database_url)
    sessions = session_factory(engine)
    queue = TaskQueue(sessions)
    recovered = await queue.recover()

    bot = create_bot(settings.bot_token)
    sender = NotificationSender(sessions, bot.send_message)  # stream 1: NFT notifications to subscribers

    # Service logs: only LOG_GROUP_ID, validated at start; independent of user notifications.
    log_sender = TelegramLogSender(
        settings.log_group_id,
        send=lambda chat_id, text: bot.send_message(chat_id, text, parse_mode=None, disable_web_page_preview=True),
        get_chat=bot.get_chat)
    log_handler = TelegramLogHandler(log_sender, settings.secrets())
    logging.getLogger().addHandler(log_handler)

    provider = KartoshkaApiProvider(settings.kartoshka_api_token, max_concurrency=settings.max_concurrent_api_requests) \
        if settings.kartoshka_api_token else None
    price = None
    if provider is not None:
        try:
            price = (await provider.pricing()).price_per_request
        except Exception as e:  # not fatal: costs still come from response headers
            log.warning("could not read /v1/pricing: %s", e)
    # Budget/402/401 warnings are logged at WARNING: stream 2 delivers them to LOG_GROUP_ID only.
    usage = ApiUsageManager(sessions, "kartoshka", settings.daily_api_budget_ton, price) \
        if provider is not None else None
    pipeline = Pipeline(sessions, queue, OwnerResolver(sessions, provider, usage, settings.api_cache_ttl),
                        owner_log=settings.owner_log_group_id is not None)

    mtproto = MTProtoClient(settings) if not settings.missing_for("mtproto") else None
    scanner = ListingScanner(mtproto, sessions, settings.listing_scan_interval, on_events=pipeline.on_events) \
        if mtproto else None

    supervisor = Supervisor()
    deps = BotDeps(settings=settings, sessions=sessions, queue=queue, pipeline=pipeline, usage=usage,
                   provider=provider, scanner=scanner, supervisor=supervisor,
                   mtproto_connected=lambda: bool(mtproto and mtproto.connected))

    async def run_scanner(stop: asyncio.Event) -> None:
        try:
            await mtproto.connect()
        except NotAuthorized as e:
            raise FatalComponentError(str(e)) from e
        try:
            await scanner.run(stop)
        finally:
            await mtproto.close()

    async def run_workers(stop: asyncio.Event) -> None:
        await asyncio.gather(*(pipeline.run_worker(stop) for _ in range(settings.max_concurrent_api_requests)))

    async def run_bot(stop: asyncio.Event) -> None:
        dp = create_dispatcher(deps)  # fresh dispatcher on every (re)start
        polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False, close_bot_session=False))
        stopper = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({polling, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if stopper in done:
            with suppress(RuntimeError):
                await dp.stop_polling()
            await polling
        else:
            stopper.cancel()
            polling.result()  # re-raise a polling crash for the supervisor

    if scanner:
        supervisor.add("scanner", run_scanner)
    supervisor.add("workers", run_workers)
    supervisor.add("notifications", sender.run)
    if settings.owner_log_group_id is not None:  # owner cards with the claim button, separate from LOG_GROUP_ID
        supervisor.add("owner_log", OwnerLogSender(sessions, bot, settings.owner_log_group_id).run)
    supervisor.add("log_sender", log_sender.run)
    supervisor.add("bot", run_bot)
    supervisor.add("backup", lambda stop: run_backups(settings.database_url, ROOT_DIR / "backups", stop))

    log.info("status: version=%s db=ok mtproto=%s bot=configured resolver=%s queue=%s recovered=%s",
             __version__, "configured" if mtproto else "not configured (API_ID/API_HASH)",
             "kartoshka ready" if provider and provider.ready else
             "kartoshka waiting for verified response schema (K4.1b)" if provider else "disabled (no token)",
             await queue.counts() or "empty", recovered)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError, RuntimeError):  # Windows: Ctrl+C arrives as KeyboardInterrupt
            loop.add_signal_handler(sig, stop.set)
    supervisor.start_all()
    try:
        await stop.wait()
    finally:
        log.info("shutting down")
        await supervisor.stop_all()
        logging.getLogger().removeHandler(log_handler)
        if provider:
            await provider.close()
        await bot.session.close()
        await engine.dispose()
        log.info("stopped")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        settings = load_settings()
        settings.require("bot")
    except ConfigError as e:
        print(f"Ошибка конфигурации: {e}", file=sys.stderr)
        return 2
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass
    except MTProtoError as e:
        print(f"MTProto: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
