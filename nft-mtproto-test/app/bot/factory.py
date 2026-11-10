"""Dispatcher wiring. The bot only reads the DB and calls services; it never talks to MTProto directly."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import admin, handlers
from app.bot.middlewares import ThrottleMiddleware, UserMiddleware
from app.config import Settings


@dataclass
class BotDeps:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    queue: Any
    pipeline: Any = None
    usage: Any = None
    provider: Any = None
    scanner: Any = None
    supervisor: Any = None
    mtproto_connected: Callable[[], bool] = field(default=lambda: False)


def create_bot(token: str) -> Bot:
    return Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def create_dispatcher(deps: BotDeps) -> Dispatcher:
    dp = Dispatcher()
    dp["deps"] = deps
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(ThrottleMiddleware())
        observer.middleware(UserMiddleware())
    dp.include_router(admin.build_router())  # admin first: its filter lets non-admin updates fall through
    dp.include_router(handlers.build_router())
    return dp
