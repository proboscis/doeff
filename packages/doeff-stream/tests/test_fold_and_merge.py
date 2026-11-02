"""Sources, merge and the fold's life: what the fold waits for, how it ends, and what it leaves behind.

- ``merge`` always carries the runtime's own inputs, once per type; ``subscribe`` refuses them.
- External events, timers and run ends reach the step as one stream.
- ``Finish`` cancels the runs still running and waits until they ended; a step that raises cancels them too.
- Two folds on one bus each get the ends of their own runs only.
- A bus handler not subscribed to the fold's inputs is named at the first wait.
"""

from dataclasses import dataclass
from datetime import timedelta

import pytest
from doeff_core_effects import Spawn, TaskCancelledError, Wait
from doeff_events import (
    ArmTimer,
    EventBus,
    Publish,
    TimerFired,
    WaitForEvent,
    WaitForEvents,
    subscribed_event_handler,
    timer_handler,
)
from doeff_stream import (
    Answered,
    Cancelled,
    Finish,
    Inputs,
    Next,
    Raised,
    Run,
    fold,
    merge,
    subscribe,
    timers,
)
from doeff_stream._source import RunEnded
from doeff_time import Delay
from stream_test_support import T0, Kick, forever, kicked, run_on_sim

from doeff import do


class Poke:
    """An external event in these checks."""

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Poke)

    def __hash__(self) -> int:
        return 1


def test_merge_carries_the_runtime_inputs_once_and_last() -> None:
    inputs = merge(subscribe(Poke, Kick), timers(), subscribe(Poke))
    assert inputs == Inputs((Poke, Kick, TimerFired, RunEnded))
    assert merge() == Inputs((RunEnded,))


@pytest.mark.parametrize("own", [Answered, Raised, Cancelled, RunEnded])
def test_subscribe_refuses_the_runtime_inputs(own: type) -> None:
    with pytest.raises(ValueError, match="runtime's own input"):
        subscribe(own)


def test_subscribe_needs_a_type() -> None:
    with pytest.raises(ValueError, match="at least one"):
        subscribe()
    with pytest.raises(TypeError):
        subscribe(Poke())  # type: ignore[arg-type]  # the check under test: a value, not a type


def test_events_timers_and_run_ends_reach_the_step_as_one_stream() -> None:
    @do
    def poke_later():
        yield Delay(60.0)
        yield Publish(Poke())

    def step(seen: tuple, event: object) -> Next[tuple]:
        seen = (*seen, event)
        match event:
            case Kick():
                return Next(
                    seen,
                    (
                        Run("arm", ArmTimer("deadline", T0 + timedelta(minutes=5))),
                        Run("poker", poke_later()),
                    ),
                )
            case TimerFired(tag="deadline"):
                return Next(seen, (Finish(seen),))
            case _:
                return Next(seen)

    seen = kicked(step, (), merge(subscribe(Kick, Poke), timers()))
    assert seen[0] == Kick()
    assert set(seen[1:-1]) == {Answered("arm", None), Poke(), Answered("poker", None)}
    assert seen.index(Poke()) < seen.index(Answered("poker", None))
    assert seen[-1] == TimerFired("deadline")


@dataclass(frozen=True)
class Unwound:
    """A run's announcement that its cancel reached it (published from its except block)."""

    key: str


@do
def waits_and_tells_its_cancel(key: str):
    try:
        return (yield forever())
    except TaskCancelledError:
        yield Publish(Unwound(key))
        raise


def observed(inputs: Inputs, body):
    """``body`` as the subscriber "observer" of the bus the fold "host" (inside it) is on: it receives ``Unwound``."""
    bus = EventBus()
    return subscribed_event_handler(bus, "observer", (Unwound,))(
        timer_handler()(body(subscribed_event_handler(bus, "host", inputs.event_types)))
    )


def test_finish_cancels_the_runs_still_running_and_waits_for_them() -> None:
    def step(state: int, event: object) -> Next[int]:
        if isinstance(event, Kick):
            runs = (
                Run("a", waits_and_tells_its_cancel("a")),
                Run("b", waits_and_tells_its_cancel("b")),
                Run("c", forever()),
            )
            return Next(1, (*runs, Finish("done")))
        return Next(state)

    inputs = merge(subscribe(Kick))

    def body(on_host):
        @do
        def program():
            task = yield Spawn(on_host(fold(step, 0, inputs)))
            yield Delay(1.0)
            yield Publish(Kick())
            answer = yield Wait(task)
            # The fold answered only after the runs unwound: both announcements are already queued.
            told = yield WaitForEvents(Unwound)
            return (answer, sorted(event.key for event in told))

        return program()

    assert run_on_sim(observed(inputs, body)) == ("done", ["a", "b"])


def test_a_step_that_raises_cancels_the_runs_still_running() -> None:
    class StepFailedError(Exception):
        pass

    def step(state: int, event: object) -> Next[int]:
        if isinstance(event, Kick):
            return Next(
                1,
                (
                    Run("r", waits_and_tells_its_cancel("r")),
                    Run("tick", ArmTimer("t", T0 + timedelta(minutes=1))),
                ),
            )
        if isinstance(event, TimerFired):
            raise StepFailedError
        return Next(state)

    inputs = merge(subscribe(Kick), timers())

    def body(on_host):
        @do
        def program():
            task = yield Spawn(on_host(fold(step, 0, inputs)))
            yield Delay(1.0)
            yield Publish(Kick())
            try:
                yield Wait(task)
            except StepFailedError:
                # The cancel reaches the run on its next turn: wait for its announcement.
                return (yield WaitForEvent(Unwound))
            return "the fold did not raise"

        return program()

    assert run_on_sim(observed(inputs, body)) == Unwound("r")


def test_two_folds_on_one_bus_get_the_ends_of_their_own_runs_only() -> None:
    def step(seen: tuple, event: object) -> Next[tuple]:
        seen = (*seen, event)
        if isinstance(event, Kick):
            return Next(
                seen,
                (
                    Run("same-key", ArmTimer(id(seen), T0 + timedelta(minutes=1))),
                    Run("wait", ArmTimer("end", T0 + timedelta(minutes=2))),
                ),
            )
        if isinstance(event, TimerFired) and event.tag == "end":
            return Next(seen, (Finish(seen),))
        return Next(seen)

    inputs = merge(subscribe(Kick), timers())
    bus = EventBus()

    @do
    def both():
        first = yield Spawn(
            subscribed_event_handler(bus, "first", inputs.event_types)(fold(step, (), inputs))
        )
        second = yield Spawn(
            subscribed_event_handler(bus, "second", inputs.event_types)(fold(step, (), inputs))
        )
        yield Delay(1.0)
        yield Publish(Kick())
        return ((yield Wait(first)), (yield Wait(second)))

    publisher = subscribed_event_handler(bus, "publisher")
    first, second = run_on_sim(publisher(timer_handler()(both())))
    for seen in (first, second):
        ends = [event for event in seen if isinstance(event, Answered)]
        assert sorted(end.key for end in ends) == ["same-key", "wait"]


def test_a_bus_not_subscribed_to_the_inputs_is_named_at_the_first_wait() -> None:
    inputs = merge(subscribe(Kick))
    body = subscribed_event_handler(EventBus(), "host", (Kick,))(
        fold(lambda s, _: Next(s), 0, inputs)
    )
    with pytest.raises(ValueError, match="RunEnded"):
        run_on_sim(body)
