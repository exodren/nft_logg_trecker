"""User registration and per-user rate limiting."""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select

from app.database.models import User
from app.database.types import utcnow

MIN_INTERVAL_S = 0.7


class UserMiddleware(BaseMiddleware):
    """Registers the user and keeps username / first_name / last_active_at fresh; passes db_user_id."""

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        tg = getattr(event, "from_user", None)
        if tg is None or tg.is_bot:
            return None
        deps = data["deps"]
        async with deps.sessions.begin() as s:
            user = (await s.execute(select(User).where(User.telegram_id == tg.id))).scalar_one_or_none()
            if user is None:
                user = User(telegram_id=tg.id)
                s.add(user)
            user.username, user.first_name = tg.username, tg.first_name
            user.is_active, user.last_active_at = True, utcnow()
            await s.flush()
            data["db_user_id"] = user.id
        return await handler(event, data)


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, min_interval_s: float = MIN_INTERVAL_S) -> None:
        self._min = min_interval_s
        self._last: dict[int, float] = {}

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        tg = getattr(event, "from_user", None)
        if tg is not None:
            now = time.monotonic()
            if now - self._last.get(tg.id, 0.0) < self._min:
                if isinstance(event, CallbackQuery):
                    await event.answer("Слишком часто, подождите секунду")
                return None
            self._last[tg.id] = now
            if len(self._last) > 10_000:  # bounded memory
                self._last.clear()
        return await handler(event, data)


__all__ = ["UserMiddleware", "ThrottleMiddleware", "Message"]
