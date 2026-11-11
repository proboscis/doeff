"""``simulate_fold``: the fold on the virtual clock and a bus of its own, the caller's handlers inside.

Counterexamples these checks name: a tool that publishes its events after the fold's first step (the step sees a run's
end before them), one that drops the caller's handlers or installs them outside the bus (a run's effect finds no
answerer, or its answerer cannot publish), one that runs on the real clock (a run's hour-long wait does not end
at once and the time it reads is not the start), a fold that spawns a run's tasks at the default priority whatever
the ``Run`` names (or a cancel's watcher below its runner), and one that starts a watcher for a run nobody cancels.
"""

from collections.abc import Generator
from dataclasses import dataclass
from datetime import timedelta

from doeff_core_effects import Spawn, TaskPriority
from doeff_events import Publish
from doeff_stream import (
    Answered,
    Cancelled,
    CancelRun,
    Finish,
    Next,
    Run,
    merge,
    simulate_fold,
    subscribe,
)
from doeff_time import Delay, GetTime
from stream_test_support import T0, Kick, forever

from doeff import EffectBase, K, Pass, Pure, Resume, do, handler


@dataclass(frozen=True)
class Poke:
    """A second published input."""

    n: int


class Lookup(EffectBase[str]):
    """The effect a run performs and the caller's simulated handler answers."""


@dataclass(frozen=True)
class LookedUp:
    """What the simulated handler publishes on the fold's bus while answering ``Lookup``."""


@handler
def simulated_lookup(effect: Lookup, k: K) -> Generator[object, object, object]:
    yield Publish(LookedUp())
    return (yield Resume(k, "found"))


def test_the_events_are_the_first_inputs_in_order() -> None:
    def step(seen: tuple, event: object) -> Next[tuple]:
        seen = (*seen, event)
        return Next(seen, (Finish(seen),)) if len(seen) == 3 else Next(seen)

    events = (Kick(), Poke(1), Poke(2))
    assert simulate_fold(step, (), merge(subscribe(Kick, Poke)), events=events) == events


def test_a_run_s_effect_is_answered_by_the_caller_s_handler_inside_the_bus() -> None:
    def step(seen: tuple, event: object) -> Next[tuple]:
        seen = (*seen, event)
        match event:
            case Kick():
                return Next(seen, (Run("lookup", Lookup()),))
            case Answered(key="lookup"):
                return Next(seen, (Finish(seen),))
            case _:
                return Next(seen)

    seen = simulate_fold(
        step,
        (),
        merge(subscribe(Kick, LookedUp)),
        events=(Kick(),),
        handlers=(simulated_lookup,),
    )
    assert seen == (Kick(), LookedUp(), Answered("lookup", "found"))


def test_the_fold_runs_on_the_virtual_clock_from_the_start_time() -> None:
    @do
    def waits_an_hour():
        yield Delay(3600.0)
        return (yield GetTime())

    def step(state: object, event: object) -> Next[object]:
        match event:
            case Kick():
                return Next(state, (Run("wait", waits_an_hour()),))
            case Answered(key="wait", value=now):
                return Next(now, (Finish(now),))
            case _:
                return Next(state)

    now = simulate_fold(step, None, merge(subscribe(Kick)), events=(Kick(),), start_time=T0)
    assert now == T0 + timedelta(hours=1)


def test_the_fold_spawns_one_task_per_run_at_its_priority_and_a_watcher_per_cancel() -> None:
    spawned: list[int] = []

    @handler
    def sees_spawn(effect: Spawn, k: K) -> Generator[object, object, object]:
        spawned.append(effect.priority)
        yield Pass(effect, k)
        return None

    def step(state: int, event: object) -> Next[int]:
        match event:
            case Kick():
                return Next(
                    state,
                    (
                        Run("screen", Pure(1), TaskPriority.HIGH),
                        Run("plain", Pure(2)),
                        Run("held", forever(), TaskPriority.HIGH),
                        CancelRun("held"),
                    ),
                )
            case Answered() | Cancelled() if state == 2:
                return Next(state + 1, (Finish(state + 1),))
            case Answered() | Cancelled():
                return Next(state + 1)
            case _:
                return Next(state)

    simulate_fold(step, 0, merge(subscribe(Kick)), events=(Kick(),), handlers=(sees_spawn,))
    # One runner per Run in order (the named priority, then the Spawn default), and the watcher of the cancelled
    # run at that run's priority: a run never cancelled has no watcher.
    assert spawned == [TaskPriority.HIGH, TaskPriority.NORMAL, TaskPriority.HIGH, TaskPriority.HIGH]
