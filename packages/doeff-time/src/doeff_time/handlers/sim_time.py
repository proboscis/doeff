"""Virtual-clock simulation handler for doeff-time effects."""


from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from doeff import Pass, Resume, Transfer, do
from doeff_core_effects import WriterTellEffect
from doeff_core_effects.scheduler import (
    CompletePromise,
    CreatePromise,
    PRIORITY_IDLE,
    Spawn,
    Wait,
)
from doeff_time._internals import SimClock, TimeQueue
from doeff_time.effects import (
    DelayEffect,
    GetTimeEffect,
    ScheduleAtEffect,
    SetTimeEffect,
    WaitUntilEffect,
)

ProtocolHandler = Callable[[Any, Any], Any]
LogFormatter = Callable[[datetime, Any], str]
EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


class SimTimeRuntime:
    """Runtime state for virtual-clock interpretation."""

    def __init__(
        self,
        *,
        start_time: datetime,
        log_formatter: LogFormatter | None,
    ) -> None:
        self._clock = SimClock(start_time)
        self._time_queue = TimeQueue()
        self._driver_running = False
        self._log_formatter = log_formatter
        self._forwarding_tell = False

        @do
        def _protocol_handler(effect: Any, k: Any):
            return (yield self.handle(effect, k))

        self._handler: ProtocolHandler = _protocol_handler

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

    @do
    def _ensure_clock_driver(self):
        if self._driver_running:
            return None
        self._driver_running = True
        yield Spawn(self._clock_driver(), priority=PRIORITY_IDLE)
        return None

    @do
    def _wait_for_time(self, target_time: datetime):
        promise = yield CreatePromise()
        self._time_queue.push(target_time, promise)
        _ = yield self._ensure_clock_driver()
        yield Wait(promise.future)

    @do
    def _handle_tell(self, effect: WriterTellEffect, k: Any):
        assert self._log_formatter is not None
        formatted = self._log_formatter(self._clock.current_time, effect.msg)
        self._forwarding_tell = True
        try:
            result = yield WriterTellEffect(formatted)
        finally:
            self._forwarding_tell = False
        yield Transfer(k, result)

    @do
    def _handle_delay(self, effect: DelayEffect, k: Any):
        target_time = self._clock.current_time + timedelta(seconds=effect.seconds)
        _ = yield self._wait_for_time(target_time)

        return (yield Resume(k, None))

    @do
    def _handle_wait_until(self, effect: WaitUntilEffect, k: Any):
        target_time = max(self._clock.current_time, effect.target)
        _ = yield self._wait_for_time(target_time)

        return (yield Resume(k, None))

    @do
    def _handle_get_time(self, _effect: GetTimeEffect, k: Any):
        return (yield Resume(k, self._clock.current_time))

    @do
    def _handle_schedule_at(self, effect: ScheduleAtEffect, k: Any):
        @do
        def deferred():
            _ = yield self._wait_for_time(effect.time)
            yield effect.program

        task = yield Spawn(deferred())
        return (yield Resume(k, task))

    @do
    def _handle_set_time(self, effect: SetTimeEffect, k: Any):
        self._clock.set_time(effect.time)
        if not self._time_queue.empty():
            _ = yield self._ensure_clock_driver()

        return (yield Resume(k, None))

    @do
    def handle(self, effect: Any, k: Any):
        if (
            isinstance(effect, WriterTellEffect)
            and self._log_formatter is not None
            and not self._forwarding_tell
        ):
            return (yield self._handle_tell(effect, k))
        if isinstance(effect, DelayEffect):
            return (yield self._handle_delay(effect, k))
        if isinstance(effect, WaitUntilEffect):
            return (yield self._handle_wait_until(effect, k))
        if isinstance(effect, GetTimeEffect):
            return (yield self._handle_get_time(effect, k))
        if isinstance(effect, ScheduleAtEffect):
            return (yield self._handle_schedule_at(effect, k))
        if isinstance(effect, SetTimeEffect):
            return (yield self._handle_set_time(effect, k))
        yield Pass(effect, k)


def sim_time_handler(
    *,
    start_time: datetime = EPOCH_UTC,
    log_formatter: LogFormatter | None = None,
) -> ProtocolHandler:
    """Return a virtual-clock handler that delegates core concurrency effects."""

    runtime = SimTimeRuntime(start_time=start_time, log_formatter=log_formatter)
    return runtime._handler


__all__ = [
    "LogFormatter",
    "ProtocolHandler",
    "sim_time_handler",
]
