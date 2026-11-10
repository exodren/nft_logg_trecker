"""DB-backed processing queue (T3.5): pending -> processing -> completed | retry | failed.

Survives restarts (recover() returns 'processing' tasks to 'pending'). Deduplicated by dedup_key among
active tasks. New listings (HIGH) are claimed before backfill (LOW).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import ProcessingTask
from app.database.types import utcnow
from app.utils.log import DATABASE

log = logging.getLogger(DATABASE)

HIGH, LOW = 10, 0
ACTIVE = ("pending", "processing", "retry")
BACKOFF_BASE_S = 30


@dataclass(frozen=True)
class Task:
    id: int
    task_type: str
    dedup_key: str
    priority: int
    attempts: int
    max_attempts: int
    listing_id: int | None
    gift_id: int | None
    payload: dict[str, Any]


def _task(row: ProcessingTask) -> Task:
    return Task(row.id, row.task_type, row.dedup_key, row.priority, row.attempts, row.max_attempts,
                row.listing_id, row.gift_id, dict(row.payload or {}))


async def enqueue(s: AsyncSession, task_type: str, dedup_key: str, *, priority: int = HIGH,
                  listing_id: int | None = None, gift_id: int | None = None,
                  payload: dict[str, Any] | None = None) -> ProcessingTask:
    """Inside the caller's transaction. An active duplicate is merged: higher priority, OR-ed flags."""
    existing = (await s.execute(select(ProcessingTask).where(
        ProcessingTask.dedup_key == dedup_key, ProcessingTask.status.in_(ACTIVE)))).scalar_one_or_none()
    if existing is not None:
        existing.priority = max(existing.priority, priority)
        merged = dict(existing.payload or {})
        for k, v in (payload or {}).items():
            merged[k] = (merged.get(k) or v) if isinstance(v, bool) else v
        existing.payload = merged
        if listing_id is not None and payload and payload.get("notify"):
            existing.listing_id = listing_id
        return existing
    row = ProcessingTask(task_type=task_type, dedup_key=dedup_key, priority=priority, status="pending",
                         listing_id=listing_id, gift_id=gift_id, payload=payload or {}, next_attempt_at=utcnow())
    s.add(row)
    await s.flush()
    return row


class TaskQueue:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], clock: Callable[[], datetime] = utcnow) -> None:
        self._sessions = sessions
        self._clock = clock

    async def claim(self) -> Task | None:
        now = self._clock()
        async with self._sessions.begin() as s:
            candidates = (await s.execute(select(ProcessingTask.id).where(
                ProcessingTask.status.in_(("pending", "retry")), ProcessingTask.next_attempt_at <= now)
                .order_by(ProcessingTask.priority.desc(), ProcessingTask.id).limit(5))).scalars().all()
            for task_id in candidates:
                # Conditional update: two workers can never take the same task.
                res = await s.execute(update(ProcessingTask).where(
                    ProcessingTask.id == task_id, ProcessingTask.status.in_(("pending", "retry")))
                    .values(status="processing", locked_at=now, attempts=ProcessingTask.attempts + 1))
                if res.rowcount == 1:
                    return _task(await s.get(ProcessingTask, task_id))
        return None

    async def complete(self, task_id: int) -> None:
        async with self._sessions.begin() as s:
            await s.execute(update(ProcessingTask).where(ProcessingTask.id == task_id)
                            .values(status="completed", locked_at=None, last_error=None))

    async def fail(self, task_id: int, error: str) -> str:
        async with self._sessions.begin() as s:
            row = await s.get(ProcessingTask, task_id)
            row.locked_at, row.last_error = None, error[:2000]
            if row.attempts >= row.max_attempts:
                row.status = "failed"
                log.error("task %s failed permanently: %s", task_id, error)
            else:
                row.status = "retry"
                row.next_attempt_at = self._clock() + timedelta(seconds=BACKOFF_BASE_S * 2 ** (row.attempts - 1))
            return row.status

    async def defer(self, task_id: int, until: datetime, reason: str) -> None:
        """Waiting for budget/quota is not a failure: the attempt is not counted."""
        async with self._sessions.begin() as s:
            row = await s.get(ProcessingTask, task_id)
            row.status, row.locked_at, row.next_attempt_at, row.last_error = "retry", None, until, f"deferred: {reason}"
            row.attempts = max(row.attempts - 1, 0)

    async def recover(self) -> int:
        async with self._sessions.begin() as s:
            res = await s.execute(update(ProcessingTask).where(ProcessingTask.status == "processing")
                                  .values(status="pending", locked_at=None))
        if res.rowcount:
            log.warning("recovered %s interrupted tasks", res.rowcount)
        return res.rowcount

    async def wake_deferred(self) -> int:
        async with self._sessions.begin() as s:
            res = await s.execute(update(ProcessingTask).where(
                ProcessingTask.status == "retry", ProcessingTask.last_error.like("deferred:%"))
                .values(next_attempt_at=self._clock()))
        return res.rowcount

    async def retry_failed(self) -> int:
        async with self._sessions.begin() as s:
            res = await s.execute(update(ProcessingTask).where(ProcessingTask.status == "failed")
                                  .values(status="pending", attempts=0, next_attempt_at=self._clock()))
        return res.rowcount

    async def counts(self) -> dict[str, int]:
        async with self._sessions() as s:
            rows = (await s.execute(select(ProcessingTask.status, func.count()).group_by(ProcessingTask.status))).all()
        return {status: n for status, n in rows}
