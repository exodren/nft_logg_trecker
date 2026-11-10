"""Runs independent components as asyncio tasks; restarts one without touching the others (T7.2, T9.1)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

log = logging.getLogger("app")

Component = Callable[[asyncio.Event], Awaitable[None]]
STOP_TIMEOUT_S = 15
CRASH_BACKOFF_S = (5, 30, 120, 300)


class FatalComponentError(RuntimeError):
    """Raise from a component that must not be restarted automatically (e.g. no authorized session)."""


@dataclass
class _Slot:
    factory: Component
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    crashes: int = 0


class Supervisor:
    def __init__(self) -> None:
        self._slots: dict[str, _Slot] = {}

    @property
    def names(self) -> list[str]:
        return list(self._slots)

    def state(self) -> dict[str, bool]:
        return {n: s.task is not None and not s.task.done() for n, s in self._slots.items()}

    def add(self, name: str, factory: Component) -> None:
        self._slots[name] = _Slot(factory)

    def start_all(self) -> None:
        for name in self._slots:
            self._start(name)

    def _start(self, name: str) -> None:
        slot = self._slots[name]
        slot.stop = asyncio.Event()
        slot.task = asyncio.create_task(self._guard(name, slot), name=name)

    async def _guard(self, name: str, slot: _Slot) -> None:
        stop = slot.stop
        while not stop.is_set():
            try:
                await slot.factory(stop)
                return
            except asyncio.CancelledError:
                raise
            except FatalComponentError as e:
                log.critical("%s stopped: %s", name, e)
                return
            except Exception:
                delay = CRASH_BACKOFF_S[min(slot.crashes, len(CRASH_BACKOFF_S) - 1)]
                slot.crashes += 1
                log.exception("%s crashed, restart in %ss", name, delay)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

    async def _stop(self, name: str) -> None:
        slot = self._slots[name]
        slot.stop.set()
        if slot.task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(slot.task), timeout=STOP_TIMEOUT_S)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            slot.task.cancel()
        except Exception:
            log.exception("%s failed while stopping", name)

    async def restart(self, name: str) -> None:
        await self._stop(name)
        self._slots[name].crashes = 0
        self._start(name)
        log.info("%s restarted", name)

    async def stop_all(self) -> None:
        await asyncio.gather(*(self._stop(n) for n in self._slots))
