"""Virtual-clock simulation handler for doeff-time effects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doeff import Pass, Resume, Spawn, Wait, WithHandler
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


def _run_due(clock: SimClock, schedule_queue: TimeQueue, handler: ProtocolHandler):
    final_time = clock.current_time
    while True:
        due = schedule_queue.pop_due(clock.current_time)
        if due is None:
            break

        clock.set_time(due.run_at)
        task = yield Spawn(WithHandler(handler, due.program))
        _ = yield Wait(task)
        final_time = max(final_time, clock.current_time)
        clock.set_time(final_time)


def sim_time_handler(
    *,
    start_time: float = 0.0,
    log_formatter: Callable[[float, Any], str] | None = None,
) -> ProtocolHandler:
    """Return a handler that interprets doeff-time effects against a virtual clock."""

    clock = SimClock(current_time=start_time)
    schedule_queue = TimeQueue()

    def maybe_format_writer_message(effect: Any) -> WriterTellEffect | None:
        if log_formatter is None:
            return None
        if not isinstance(effect, WriterTellEffect):
            return None
        formatted = log_formatter(clock.current_time, effect.message)
        return WriterTellEffect(formatted)

    def handler(effect: Any, k: Any):
        replacement = maybe_format_writer_message(effect)
        if replacement is not None:
            yield Pass(replacement)
            return

        if isinstance(effect, DelayEffect):
            clock.advance_by(effect.seconds)
            yield from _run_due(clock, schedule_queue, handler)
            return (yield Resume(k, None))

        if isinstance(effect, WaitUntilEffect):
            clock.advance_to(effect.target)
            yield from _run_due(clock, schedule_queue, handler)
            return (yield Resume(k, None))

        if isinstance(effect, GetTimeEffect):
            return (yield Resume(k, clock.current_time))

        if isinstance(effect, ScheduleAtEffect):
            schedule_queue.push(effect.time, effect.program)
            if effect.time <= clock.current_time:
                yield from _run_due(clock, schedule_queue, handler)
            return (yield Resume(k, None))

        if isinstance(effect, SetTimeEffect):
            clock.set_time(effect.time)
            yield from _run_due(clock, schedule_queue, handler)
            return (yield Resume(k, None))

        yield Pass()

    return handler


__all__ = [
    "ProtocolHandler",
    "sim_time_handler",
]
