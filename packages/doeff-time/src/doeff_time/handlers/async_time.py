"""Asyncio-backed wall-clock handler for doeff-time effects."""


import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from doeff_core_effects import Await
from doeff_core_effects.scheduler import PRIORITY_IDLE, Cancel, Race, Spawn

from doeff import Pass, Transfer, do
from doeff import handler as _program_handler
from doeff.program import ProgramHandler
from doeff_time.effects import (
    DelayEffect,
    GetMonotonicEffect,
    GetTimeEffect,
    ScheduleAtEffect,
    WaitTicksEffect,
    WaitUntilEffect,
    WaitWithinEffect,
)
from doeff_time.handlers._wall_ticks import timed_wait_answer, timed_wait_seconds

ProtocolHandler = Callable[[Any, Any], Any]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clock_wait(sleep: Callable[[float], Awaitable[Any]], seconds: float) -> Await:
    """An Await whose completion time is known: the scheduler does not count
    it as stalled until ``seconds`` have passed (agora-redesign #765). The
    deadline is on the scheduler's clock (``time.monotonic``), not on the
    injectable ``monotonic`` that answers GetMonotonic."""
    return Await(sleep(seconds), deadline=time.monotonic() + seconds)


class AsyncTimeRuntime:
    """Runtime container for async wall-clock time effects."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[Any]],
        monotonic: Callable[[], float],
    ) -> None:
        self._now = now
        self._sleep = sleep
        self._monotonic = monotonic

    @do
    def _expire_after(self, seconds: float):
        """The deadline of a timed wait (WaitWithin): sleep ``seconds`` on the wall clock, then answer None."""
        yield _clock_wait(self._sleep, max(0.0, seconds))
        return None

    @do
    def handle(self, effect: Any, k: Any):
        # Every clause performs its final Transfer/Pass from THIS frame.
        # Delegating to a sub-@do that transfers (the pre-2026-07-14 shape)
        # leaves this frame suspended mid-`yield` forever, pinning the
        # Task handle and defeating the scheduler's terminal-entry sweep
        # (ADR-DOE-CORE-EFFECTS-002).
        if isinstance(effect, DelayEffect):
            yield _clock_wait(self._sleep, max(0.0, effect.seconds))
            return (yield Transfer(k, None))
        if isinstance(effect, WaitUntilEffect):
            wait_seconds = max(0.0, (effect.target - self._now()).total_seconds())
            yield _clock_wait(self._sleep, wait_seconds)
            return (yield Transfer(k, None))
        if isinstance(effect, GetTimeEffect):
            return (yield Transfer(k, self._now()))
        if isinstance(effect, GetMonotonicEffect):
            return (yield Transfer(k, float(self._monotonic())))
        if isinstance(effect, (WaitWithinEffect, WaitTicksEffect)):
            # The deadline is a daemon task sleeping on the wall clock, raced against the future and
            # cancelled afterwards (daemon: abandoning it at root return is its lifecycle — #501).
            # WaitTicks' deadline is its last tick (agora-redesign #3066).
            started = float(self._monotonic())
            timer = yield Spawn(self._expire_after(timed_wait_seconds(effect)), daemon=True)
            try:
                first = yield Race(effect.future, timer, priority=PRIORITY_IDLE if effect.park else None)
            finally:
                yield Cancel(timer)
            return (yield Transfer(k, timed_wait_answer(effect, first, float(self._monotonic()) - started)))
        if isinstance(effect, ScheduleAtEffect):
            wait_seconds = max(0.0, (effect.time - self._now()).total_seconds())
            sleep = self._sleep

            @do
            def deferred():
                yield _clock_wait(sleep, wait_seconds)
                # Wait(task) answers the scheduled program's value (agora-redesign #1159).
                return (yield effect.program)

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
    monotonic: Callable[[], float] = time.monotonic,
) -> ProgramHandler:
    """Return a protocol handler for wall-clock async time semantics."""

    runtime = AsyncTimeRuntime(now=now, sleep=sleep, monotonic=monotonic)
    return _program_handler(runtime.handle)

