"""``Arm`` / ``Disarm``: a step sets and takes back a deadline of its fold without a ``Run``.

- The deadline of an ``Arm`` comes back to the step as ``TimerFired(tag)`` at its instant, and no run end comes with
  it (no task was started).
- A ``Disarm`` keeps its ``TimerFired`` from coming.
- Two folds on one bus and one timer handler, arming the same tag: each gets its own deadline only.
- Each of the three for a plain step and for a step that answers a Program.
- The fold refuses an ``Arm`` without ``timers()`` among its sources, and disarms the deadlines still set when it ends.
- The replay carries ``Arm`` / ``Disarm`` in its effects and tells the tags still set.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from doeff_core_effects import Spawn, Wait
from doeff_events import (
    ArmedTimers,
    EventBus,
    Publish,
    TimerFired,
    subscribed_event_handler,
    timer_handler,
)
from doeff_stream import (
    Arm,
    Disarm,
    Finish,
    Inputs,
    Next,
    fold,
    merge,
    replay,
    simulate_fold,
    subscribe,
    timers,
)
from doeff_time import Delay, GetTime
from stream_test_support import T0, Kick, on_bus, run_on_sim

from doeff import Program, do

Seen = tuple[object, ...]
Step = Callable[[Seen, object], "Next[Seen] | Program[Next[Seen]]"]


def as_program(step: Callable[[Seen, object], Next[Seen]]) -> Step:
    """``step`` as a step that answers a Program composing another Program (the shape of a defk reducer)."""

    @do
    def answer(seen: Seen, event: object):
        return step(seen, event)

    @do
    def program_step(seen: Seen, event: object):
        return (yield answer(seen, event))

    return program_step


def plain(step: Callable[[Seen, object], Next[Seen]]) -> Step:
    return step


SHAPES = pytest.mark.parametrize("shaped", [plain, as_program], ids=["plain", "program"])

INPUTS = merge(subscribe(Kick), timers())


@do
def _then_time(body: Program[object]):
    """``body``'s value and the clock's time once it answered."""
    value = yield body
    return value, (yield GetTime())


@dataclass(frozen=True)
class _Kicked:
    """What the fold answered, and the time it answered at."""

    value: object
    at: datetime


def _kicked_with_time(step: Step, inputs: Inputs) -> _Kicked:
    """``kicked``, and the time the fold answered at."""

    @do
    def kick_then():
        task = yield Spawn(fold(step, (), inputs))
        yield Delay(1.0)
        yield Publish(Kick())
        return (yield Wait(task))

    answered = run_on_sim(on_bus(EventBus(), "host", inputs, _then_time(kick_then())))
    assert isinstance(answered, tuple)
    value, at = answered
    assert isinstance(at, datetime)
    return _Kicked(value, at)


@SHAPES
def test_the_deadline_of_an_arm_comes_back_as_timer_fired(
    shaped: Callable[[Callable[[Seen, object], Next[Seen]]], Step],
) -> None:
    def step(seen: Seen, event: object) -> Next[Seen]:
        seen = (*seen, event)
        match event:
            case Kick():
                return Next(seen, (Arm("deadline", T0 + timedelta(minutes=5)),))
            case TimerFired(tag="deadline"):
                return Next(seen, (Finish(seen),))
            case _:
                return Next(seen)

    kicked = _kicked_with_time(shaped(step), INPUTS)
    # No run end among the inputs: the deadline was set without a task.
    assert kicked.value == (Kick(), TimerFired("deadline"))
    assert kicked.at == T0 + timedelta(minutes=5)


@SHAPES
def test_a_disarmed_deadline_does_not_come(
    shaped: Callable[[Callable[[Seen, object], Next[Seen]]], Step],
) -> None:
    def step(seen: Seen, event: object) -> Next[Seen]:
        seen = (*seen, event)
        match event:
            case Kick():
                return Next(
                    seen,
                    (
                        Arm("late", T0 + timedelta(minutes=5)),
                        Arm("early", T0 + timedelta(minutes=1)),
                    ),
                )
            case TimerFired(tag="early"):
                return Next(seen, (Disarm("late"), Arm("end", T0 + timedelta(minutes=10))))
            case TimerFired(tag="end"):
                return Next(seen, (Finish(seen),))
            case _:
                return Next(seen)

    assert simulate_fold(shaped(step), (), INPUTS, events=(Kick(),), start_time=T0) == (
        Kick(),
        TimerFired("early"),
        TimerFired("end"),
    )


@SHAPES
def test_the_deadline_of_another_fold_does_not_reach_the_step(
    shaped: Callable[[Callable[[Seen, object], Next[Seen]]], Step],
) -> None:
    # Both folds arm the tag "end" in the one timer handler: each gets its own deadline, at its own instant.
    def ending_at(minutes: int) -> Callable[[Seen, object], Next[Seen]]:
        def step(seen: Seen, event: object) -> Next[Seen]:
            seen = (*seen, event)
            match event:
                case Kick():
                    return Next(seen, (Arm("end", T0 + timedelta(minutes=minutes)),))
                case TimerFired(tag="end"):
                    return Next(seen, (Finish(seen),))
                case _:
                    return Next(seen)

        return step

    bus = EventBus()

    @do
    def both():
        first = yield Spawn(
            subscribed_event_handler(bus, "first", INPUTS.event_types)(
                _then_time(fold(shaped(ending_at(5)), (), INPUTS))
            )
        )
        second = yield Spawn(
            subscribed_event_handler(bus, "second", INPUTS.event_types)(
                _then_time(fold(shaped(ending_at(10)), (), INPUTS))
            )
        )
        yield Delay(1.0)
        yield Publish(Kick())
        return ((yield Wait(first)), (yield Wait(second)))

    publisher = subscribed_event_handler(bus, "publisher")
    assert run_on_sim(publisher(timer_handler()(both()))) == (
        ((Kick(), TimerFired("end")), T0 + timedelta(minutes=5)),
        ((Kick(), TimerFired("end")), T0 + timedelta(minutes=10)),
    )


def test_an_arm_without_timers_among_the_sources_is_refused() -> None:
    def step(seen: Seen, event: object) -> Next[Seen]:
        return Next(seen, (Arm("deadline", T0 + timedelta(minutes=5)),))

    with pytest.raises(ValueError, match="without timers"):
        simulate_fold(step, (), merge(subscribe(Kick)), events=(Kick(),), start_time=T0)


def test_the_fold_disarms_the_deadlines_still_set_when_it_ends() -> None:
    def step(seen: Seen, event: object) -> Next[Seen]:
        return Next(
            seen,
            (Arm("left", T0 + timedelta(minutes=5)), Disarm("never-armed"), Finish("done")),
        )

    @do
    def kick_then_list():
        task = yield Spawn(fold(step, (), INPUTS))
        yield Delay(1.0)
        yield Publish(Kick())
        value = yield Wait(task)
        return value, (yield ArmedTimers())

    assert run_on_sim(on_bus(EventBus(), "host", INPUTS, kick_then_list())) == ("done", ())


def test_an_arm_takes_a_timezone_aware_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Arm("deadline", datetime(2026, 1, 1))  # noqa: DTZ001 — the naive datetime under test
    with pytest.raises(TypeError, match="takes a datetime"):
        Arm("deadline", 5)  # type: ignore[arg-type]  # the check under test


def test_replay_carries_arm_and_disarm_and_tells_the_tags_still_set() -> None:
    def step(seen: Seen, event: object) -> Next[Seen]:
        match event:
            case Kick():
                return Next(
                    seen,
                    (Arm("a", T0 + timedelta(minutes=1)), Arm("b", T0 + timedelta(minutes=2))),
                )
            case TimerFired(tag="a"):
                return Next(seen, (Disarm("b"), Arm("c", T0 + timedelta(minutes=3))))
            case _:
                return Next(seen)

    trace = replay(step, (), [Kick(), TimerFired("a")])
    assert trace.effects == (
        Arm("a", T0 + timedelta(minutes=1)),
        Arm("b", T0 + timedelta(minutes=2)),
        Disarm("b"),
        Arm("c", T0 + timedelta(minutes=3)),
    )
    assert trace.armed == frozenset({"c"})
    assert replay(step, (), [Kick()]).armed == frozenset({"a", "b"})
