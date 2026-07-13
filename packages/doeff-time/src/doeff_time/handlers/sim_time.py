"""Virtual-clock simulation handler for doeff-time effects."""


from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from doeff_core_effects import WriterTellEffect
from doeff_core_effects.scheduler import (
    PRIORITY_IDLE,
    CompletePromise,
    CreatePromise,
    Spawn,
    Wait,
)

from doeff import Pass, Transfer, do
from doeff import handler as _program_handler
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
        self._handler: ProtocolHandler = self.handle

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
        # daemon=True carries two contracts:
        # - #501: the driver's final IDLE resume (queued right after its
        #   last CompletePromise) is routinely abandoned when the root body
        #   returns first — that is this daemon's lifecycle, not lost work,
        #   so it must not trip the root close-out diagnostic.
        # - #505: daemon tasks are the only tasks the scheduler's
        #   PRIORITY_EXTERNAL_WAIT shield may starve, which is exactly what
        #   keeps this driver from advancing sim time past a pending
        #   external completion.
        yield Spawn(self._clock_driver(), priority=PRIORITY_IDLE, daemon=True)
        return None

    @do
    def _wait_for_time(self, target_time: datetime):
        promise = yield CreatePromise()
        self._time_queue.push(target_time, promise)
        _ = yield self._ensure_clock_driver()
        yield Wait(promise.future)

    @do
    def handle(self, effect: Any, k: Any):
        # Every clause performs its final Transfer/Pass from THIS frame.
        # Delegating to a sub-@do that transfers (the pre-2026-07-14 shape)
        # leaves this frame suspended mid-`yield` forever, pinning the
        # Task handle and defeating the scheduler's terminal-entry sweep
        # (ADR-DOE-CORE-EFFECTS-002). Sub-programs that COMPLETE before
        # the Transfer (_wait_for_time, _ensure_clock_driver) are fine.
        if (
            isinstance(effect, WriterTellEffect)
            and self._log_formatter is not None
            and not self._forwarding_tell
        ):
            formatted = self._log_formatter(self._clock.current_time, effect.msg)
            self._forwarding_tell = True
            try:
                result = yield WriterTellEffect(formatted)
            finally:
                self._forwarding_tell = False
            return (yield Transfer(k, result))
        if isinstance(effect, DelayEffect):
            target_time = self._clock.current_time + timedelta(seconds=effect.seconds)
            _ = yield self._wait_for_time(target_time)
            return (yield Transfer(k, None))
        if isinstance(effect, WaitUntilEffect):
            target_time = max(self._clock.current_time, effect.target)
            _ = yield self._wait_for_time(target_time)
            return (yield Transfer(k, None))
        if isinstance(effect, GetTimeEffect):
            return (yield Transfer(k, self._clock.current_time))
        if isinstance(effect, ScheduleAtEffect):

            @do
            def deferred():
                _ = yield self._wait_for_time(effect.time)
                yield effect.program

            task = yield Spawn(deferred())
            return (yield Transfer(k, task))
        if isinstance(effect, SetTimeEffect):
            self._clock.set_time(effect.time)
            if not self._time_queue.empty():
                _ = yield self._ensure_clock_driver()
            return (yield Transfer(k, None))
        yield Pass(effect, k)


def sim_time_handler(
    *,
    start_time: datetime = EPOCH_UTC,
    log_formatter: LogFormatter | None = None,
) -> ProtocolHandler:
    """Return a virtual-clock handler that delegates core concurrency effects."""

    runtime = SimTimeRuntime(start_time=start_time, log_formatter=log_formatter)
    return _program_handler(runtime._handler)


__all__ = [
    "LogFormatter",
    "ProtocolHandler",
    "sim_time_handler",
]
