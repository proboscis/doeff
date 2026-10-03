"""Blocking wall-clock handler for doeff-time effects."""


import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from doeff_core_effects.scheduler import PRIORITY_IDLE, CreateExternalPromise, Race, Spawn
from doeff_core_effects.scheduler import Wait as WaitTask

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


class SyncTimeRuntime:
    """Runtime container for sync wall-clock time effects."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime],
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
    ) -> None:
        self._now = now
        self._sleep = sleep
        self._monotonic = monotonic

    @do
    def handle(self, effect: Any, k: Any):
        # Every clause performs its final Transfer/Pass from THIS frame.
        # Delegating to a sub-@do that transfers (the pre-2026-07-14 shape)
        # leaves this frame suspended mid-`yield` forever, pinning the
        # Task handle and defeating the scheduler's terminal-entry sweep
        # (ADR-DOE-CORE-EFFECTS-002).
        if isinstance(effect, DelayEffect):
            self._sleep(max(0.0, effect.seconds))
            return (yield Transfer(k, None))
        if isinstance(effect, WaitUntilEffect):
            self._sleep(max(0.0, (effect.target - self._now()).total_seconds()))
            return (yield Transfer(k, None))
        if isinstance(effect, GetTimeEffect):
            return (yield Transfer(k, self._now()))
        if isinstance(effect, GetMonotonicEffect):
            return (yield Transfer(k, float(self._monotonic())))
        if isinstance(effect, (WaitWithinEffect, WaitTicksEffect)):
            # The deadline is an external promise a wall-clock timer completes (the scheduler knows
            # when it wakes — #765), raced against the future; the timer is stopped afterwards.
            # WaitTicks' deadline is its last tick (agora-redesign #3066).
            started = float(self._monotonic())
            seconds = timed_wait_seconds(effect)
            deadline = yield CreateExternalPromise(deadline=time.monotonic() + seconds)

            def _deadline_passed():
                deadline.complete(None)

            timer = threading.Timer(seconds, _deadline_passed)
            timer.daemon = True
            timer.start()
            try:
                first = yield Race(effect.future, deadline.future, priority=PRIORITY_IDLE if effect.park else None)
            finally:
                timer.cancel()
            return (yield Transfer(k, timed_wait_answer(effect, first, float(self._monotonic()) - started)))
        if isinstance(effect, ScheduleAtEffect):
            wait_seconds = max(0.0, (effect.time - self._now()).total_seconds())

            @do
            def deferred():
                # A timer wait: the scheduler knows when it wakes (#765).
                ep = yield CreateExternalPromise(deadline=time.monotonic() + wait_seconds)

                def _timer_done():
                    ep.complete(None)

                timer = threading.Timer(wait_seconds, _timer_done)
                timer.daemon = True
                timer.start()
                yield WaitTask(ep.future)
                # Wait(task) answers the scheduled program's value (agora-redesign #1159).
                return (yield effect.program)

            # Resume the caller with the spawned Task (same contract as
            # sim_time_handler) so failures of the deferred program can be
            # observed via Wait/Gather instead of vanishing on an unwatched
            # task (#503).
            task = yield Spawn(deferred())
            return (yield Transfer(k, task))
        yield Pass(effect, k)


def sync_time_handler(
    *,
    now: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> ProgramHandler:
    """Return a protocol handler for blocking wall-clock time semantics."""

    runtime = SyncTimeRuntime(now=now, sleep=sleep, monotonic=monotonic)
    return _program_handler(runtime.handle)

