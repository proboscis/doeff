"""Asyncio-backed wall-clock handler for doeff-time effects."""


import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from doeff_core_effects import Await
from doeff_core_effects.scheduler import Spawn

from doeff import Pass, Transfer, do
from doeff import handler as _program_handler
from doeff_time.effects import DelayEffect, GetTimeEffect, ScheduleAtEffect, WaitUntilEffect

ProtocolHandler = Callable[[Any, Any], Any]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AsyncTimeRuntime:
    """Runtime container for async wall-clock time effects."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[Any]],
    ) -> None:
        self._now = now
        self._sleep = sleep

    @do
    def handle(self, effect: Any, k: Any):
        # Every clause performs its final Transfer/Pass from THIS frame.
        # Delegating to a sub-@do that transfers (the pre-2026-07-14 shape)
        # leaves this frame suspended mid-`yield` forever, pinning the
        # Task handle and defeating the scheduler's terminal-entry sweep
        # (ADR-DOE-CORE-EFFECTS-002).
        if isinstance(effect, DelayEffect):
            yield Await(self._sleep(max(0.0, effect.seconds)))
            return (yield Transfer(k, None))
        if isinstance(effect, WaitUntilEffect):
            wait_seconds = max(0.0, (effect.target - self._now()).total_seconds())
            yield Await(self._sleep(wait_seconds))
            return (yield Transfer(k, None))
        if isinstance(effect, GetTimeEffect):
            return (yield Transfer(k, self._now()))
        if isinstance(effect, ScheduleAtEffect):
            wait_seconds = max(0.0, (effect.time - self._now()).total_seconds())
            sleep = self._sleep

            @do
            def deferred():
                yield Await(sleep(wait_seconds))
                yield effect.program

            # Resume the caller with the spawned Task (same contract as
            # sim_time_handler) so failures of the deferred program can be
            # observed via Wait/Gather instead of vanishing on an unwatched
            # task (#503).
            task = yield Spawn(deferred())
            return (yield Transfer(k, task))
        yield Pass(effect, k)


def async_time_handler(
    *,
    now: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> ProtocolHandler:
    """Return a protocol handler for wall-clock async time semantics."""

    runtime = AsyncTimeRuntime(now=now, sleep=sleep)
    return _program_handler(runtime.handle)


__all__ = [
    "ProtocolHandler",
    "async_time_handler",
]
