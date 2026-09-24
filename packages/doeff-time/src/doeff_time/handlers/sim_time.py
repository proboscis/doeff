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
    GetMonotonicEffect,
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
        clock: SimClock,
        log_formatter: LogFormatter | None,
    ) -> None:
        self._clock = clock
        self._time_queue = TimeQueue()
        self._mut_driver_running = False
        self._log_formatter = log_formatter
        self._mut_forwarding_tell = False
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
            self._mut_driver_running = False

    @do
    def _ensure_clock_driver(self):
        if self._mut_driver_running:
            return None
        self._mut_driver_running = True
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
            and not self._mut_forwarding_tell
        ):
            formatted = self._log_formatter(self._clock.current_time, effect.msg)
            self._mut_forwarding_tell = True
            try:
                result = yield WriterTellEffect(formatted)
            finally:
                self._mut_forwarding_tell = False
            return (yield Transfer(k, result))
        if isinstance(effect, DelayEffect):
            target_time = self._clock.current_time + timedelta(seconds=effect.seconds)
            _ = yield self._wait_for_time(target_time)
            return (yield Transfer(k, None))
        if isinstance(effect, WaitUntilEffect):
            target_time = max(self._clock.current_time, effect.target)
            _ = yield self._wait_for_time(target_time)
            return (yield Transfer(k, None))
        if isinstance(effect, (GetTimeEffect, GetMonotonicEffect)):
            now = self._clock.current_time
            # GetMonotonic reads the virtual clock's POSIX timestamp: virtual
            # time only moves through Delay/WaitUntil/SetTime, and callers use
            # differences between readings.
            reading = now if isinstance(effect, GetTimeEffect) else now.timestamp()
            return (yield Transfer(k, reading))
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
    start_time: datetime | None = None,
    clock: SimClock | None = None,
    log_formatter: LogFormatter | None = None,
) -> ProtocolHandler:
    """Return a virtual-clock handler that delegates core concurrency effects.

    Install it inside ``scheduled`` and outside every ``Spawn`` whose tasks
    sleep on it: spawned tasks inherit the handler, so all of them share one
    virtual clock. Time advances to the earliest pending wake-up only when no
    normal-priority task is runnable; wake-ups at the same instant resume in
    the order they were requested.

    ``clock`` lets the caller own the ``SimClock`` so tests can read
    ``clock.current_time`` synchronously (or move it with ``set_time`` between
    runs). Pass either ``start_time`` or ``clock``, not both.
    """

    if clock is not None and start_time is not None:
        raise ValueError("pass either start_time or clock, not both")
    if clock is None:
        clock = SimClock(EPOCH_UTC if start_time is None else start_time)
    runtime = SimTimeRuntime(clock=clock, log_formatter=log_formatter)
    return _program_handler(runtime._handler)

