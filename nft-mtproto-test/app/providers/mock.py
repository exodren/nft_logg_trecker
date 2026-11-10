"""MockProvider: owner data without network, for tests and for running the app before the token exists."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from app.database.types import utcnow
from app.providers.base import OwnerData, OwnerStatus


class MockProvider:
    name = "mock"

    def __init__(self, answers: dict[str, OwnerData | OwnerStatus] | None = None,
                 default: OwnerStatus = OwnerStatus.NOT_FOUND, clock: Callable[[], datetime] = utcnow) -> None:
        self._answers = dict(answers or {})
        self._default = default
        self._clock = clock
        self.calls: list[str] = []

    @property
    def ready(self) -> bool:
        return True

    def set(self, ref: str, answer: OwnerData | OwnerStatus) -> None:
        self._answers[ref] = answer

    async def lookup(self, ref: str) -> OwnerData:
        self.calls.append(ref)
        answer = self._answers.get(ref, self._default)
        if isinstance(answer, OwnerData):
            return answer
        return OwnerData(status=answer, provider=self.name, fetched_at=self._clock(),
                         is_hidden=answer is OwnerStatus.HIDDEN)
