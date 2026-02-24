"""Virtual-clock simulation handler for doeff-time effects."""

from __future__ import annotations

import heapq
from collections.abc import Callable
from typing import Any

from doeff import (
    PRIORITY_IDLE,
    CompletePromise,
    CreatePromise,
    Pass,
    Resume,
    Spawn,
    Wait,
    do,
)
from doeff.effects.writer import WriterTellEffect
from doeff_time._internals import SimClock, TimeQueue
from doeff_time.effects import (
    DelayEffect,
    GetTimeEffect,
    ScheduleAtEffect,
    SetTimeEffect,
    WaitUntilEffect,
)

ProtocolHandler = Callable[[Any, Any], Any]
LogFormatter = Callable[[float, Any], str]


class SimTimeRuntime:
    """Runtime state for virtual-clock interpretation."""

    def __init__(
        self,
        *,
        start_time: float,
        log_formatter: LogFormatter | None,
    ) -> None:
        self._clock = SimClock(start_time)
        self._time_queue = TimeQueue()
        self._driver_running = False
        self._log_formatter = log_formatter
        self._schedule_sequence = 0
        self._scheduled_programs: list[tuple[float, int, Any]] = []

    @do
    def _clock_driver(self):
        """Idle-priority daemon that advances time when normal tasks are parked."""

        try:
            while not self._time_queue.empty():
                entry = self._time_queue.pop()
                self._clock.advance_to(entry.time)
                yield CompletePromise(entry.promise, None)
        finally:
            self._driver_running = False

    def _ensure_clock_driver(self):
        if self._driver_running:
            return None
        self._driver_running = True
        yield Spawn(self._clock_driver(), priority=PRIORITY_IDLE)
        return None

    def _wait_for_time(self, target_time: float):
        promise = yield CreatePromise()
        self._time_queue.push(target_time, promise)
        yield from self._ensure_clock_driver()
        yield Wait(promise.future)

    def _run_due_scheduled_programs(self):
        while self._scheduled_programs and self._scheduled_programs[0][0] <= self._clock.current_time:
            _time, _sequence, program = heapq.heappop(self._scheduled_programs)
            yield program

    def _handle_tell(self, effect: WriterTellEffect, k):
        assert self._log_formatter is not None
        formatted = self._log_formatter(self._clock.current_time, effect.message)
        result = yield WriterTellEffect(formatted)
        return (yield Resume(k, result))

    def _handle_delay(self, effect: DelayEffect, k):
        target_time = self._clock.current_time + effect.seconds
        yield from self._wait_for_time(target_time)
        yield from self._run_due_scheduled_programs()
        return (yield Resume(k, None))

    def _handle_wait_until(self, effect: WaitUntilEffect, k):
        target_time = max(self._clock.current_time, effect.target)
        yield from self._wait_for_time(target_time)
        yield from self._run_due_scheduled_programs()
        return (yield Resume(k, None))

    def _handle_get_time(self, _effect: GetTimeEffect, k):
        return (yield Resume(k, self._clock.current_time))

    def _handle_schedule_at(self, effect: ScheduleAtEffect, k):
        self._schedule_sequence += 1
        heapq.heappush(
            self._scheduled_programs,
            (effect.time, self._schedule_sequence, effect.program),
        )
        return (yield Resume(k, None))

    def _handle_set_time(self, effect: SetTimeEffect, k):
        self._clock.set_time(effect.time)
        if not self._time_queue.empty():
            yield from self._ensure_clock_driver()
        yield from self._run_due_scheduled_programs()
        return (yield Resume(k, None))

    def handle(self, effect: Any, k: Any):
        if isinstance(effect, WriterTellEffect) and self._log_formatter is not None:
            return (yield from self._handle_tell(effect, k))
        if isinstance(effect, DelayEffect):
            return (yield from self._handle_delay(effect, k))
        if isinstance(effect, WaitUntilEffect):
            return (yield from self._handle_wait_until(effect, k))
        if isinstance(effect, GetTimeEffect):
            return (yield from self._handle_get_time(effect, k))
        if isinstance(effect, ScheduleAtEffect):
            return (yield from self._handle_schedule_at(effect, k))
        if isinstance(effect, SetTimeEffect):
            return (yield from self._handle_set_time(effect, k))
        yield Pass()


def sim_time_handler(
    *,
    start_time: float = 0.0,
    log_formatter: LogFormatter | None = None,
) -> ProtocolHandler:
    """Return a virtual-clock handler that delegates core concurrency effects."""

    runtime = SimTimeRuntime(start_time=start_time, log_formatter=log_formatter)
    return runtime.handle


__all__ = [
    "LogFormatter",
    "ProtocolHandler",
    "sim_time_handler",
]
