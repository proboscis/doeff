"""Virtual-time simulation handler for doeff-time effects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doeff import (
    PRIORITY_IDLE,
    PRIORITY_NORMAL,
    CompletePromise,
    Delegate,
    CreatePromise,
    Gather,
    ProgramBase,
    Resume,
    Spawn,
    WriterTellEffect,
    do,
)
from doeff.effects import CompletePromiseEffect
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


def sim_time_handler(
    *,
    start_time: float = 0.0,
    log_formatter: LogFormatter | None = None,
) -> ProtocolHandler:
    """Return a protocol handler for virtual-time semantics.

    The handler delegates concurrency effects to the core scheduler and only
    interprets doeff-time effects. A clock driver daemon is spawned at
    PRIORITY_IDLE so virtual time advances only when normal-priority tasks are
    parked or complete.
    """

    clock = SimClock(_mut_current_time=start_time)
    time_queue = TimeQueue()
    driver_running = False

    @do
    def clock_driver() -> Any:
        nonlocal driver_running
        try:
            while True:
                entry = time_queue.pop()
                if entry is None:
                    return None

                clock.advance_to(entry.time)

                if entry.promise is not None:
                    yield CompletePromise(entry.promise, None)
                    tick = yield Spawn(normal_tick(), priority=PRIORITY_NORMAL)
                    _ = yield Gather(tick)
                    continue

                if entry.program is not None:
                    _ = yield Spawn(entry.program, priority=PRIORITY_NORMAL)
        finally:
            driver_running = False

    @do
    def ensure_clock_driver() -> Any:
        nonlocal driver_running
        if driver_running or time_queue.empty():
            return None
        driver_running = True
        _ = yield Spawn(clock_driver(), priority=PRIORITY_IDLE)
        return None

    @do
    def normal_tick() -> Any:
        return None

    def _formatted_message(effect: Any) -> str | None:
        if log_formatter is None or not isinstance(effect, WriterTellEffect):
            return None
        return log_formatter(clock.current_time, effect.message)

    def handler(effect: Any, k: Any) -> Any:
        formatted = _formatted_message(effect)
        if formatted is not None:
            tell_result = yield WriterTellEffect(formatted)
            return (yield Resume(k, tell_result))

        if isinstance(effect, DelayEffect):
            promise = yield CreatePromise()
            time_queue.push_promise(clock.current_time + effect.seconds, promise)
            yield ensure_clock_driver()
            _ = yield Gather(promise.future)
            return (yield Resume(k, None))

        if isinstance(effect, WaitUntilEffect):
            promise = yield CreatePromise()
            target = max(clock.current_time, effect.target)
            time_queue.push_promise(target, promise)
            yield ensure_clock_driver()
            _ = yield Gather(promise.future)
            return (yield Resume(k, None))

        if isinstance(effect, GetTimeEffect):
            return (yield Resume(k, clock.current_time))

        if isinstance(effect, ScheduleAtEffect):
            target = max(clock.current_time, effect.time)
            time_queue.push_program(target, effect.program)
            yield ensure_clock_driver()
            return (yield Resume(k, None))

        if isinstance(effect, SetTimeEffect):
            clock.jump_to(effect.time)
            yield ensure_clock_driver()
            return (yield Resume(k, None))

        if isinstance(effect, ProgramBase | CompletePromiseEffect):
            yield Delegate()
            return None

        delegated = yield Delegate()
        return (yield Resume(k, delegated))

    return handler
