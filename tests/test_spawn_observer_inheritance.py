"""Spawn must inherit WithObserve boundaries like it inherits handlers.

Issue: scheduler-spawn-drops-observer-boundary

The scheduler's Spawn handler captures inner handlers from the continuation
(get_inner_handlers) and reinstalls them on the child task, but it drops
intercept/observer boundaries. An observer installed inside scheduled()
therefore never sees effects performed by spawned tasks — a silent drop.

Expected semantics (mirrors handler inheritance): every observer boundary
between the spawn site and the scheduler is captured at Spawn time and
reinstalled around the child program, so subtree observation crosses Spawn.
Observers outside scheduled() keep working unchanged (they remain on the
fiber parent chain).
"""

import dataclasses
import threading
from typing import Any

from doeff_core_effects.scheduler import (
    Cancel,
    CreatePromise,
    Spawn,
    Wait,
    scheduled,
)

from doeff import EffectBase, Pass, Resume, WithObserve, do, handler, run

SCHEDULER_TIMEOUT_SECONDS = 5


@dataclasses.dataclass(frozen=True)
class Mark(EffectBase):
    label: str


@handler
@do
def mark_handler(effect, k):
    if isinstance(effect, Mark):
        return (yield Resume(k, None))
    return (yield Pass(effect, k))


def make_observer(sink: list[str]):
    def observer(effect):
        if isinstance(effect, Mark):
            sink.append(effect.label)

    return observer


def run_with_timeout(program: Any) -> Any:
    """Run in a worker thread so a scheduler hang fails the test, not CI."""
    outcome: dict[str, Any] = {}

    def _worker() -> None:
        try:
            outcome["value"] = run(program)
        except BaseException as exc:  # pragma: no cover - test helper
            outcome["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=SCHEDULER_TIMEOUT_SECONDS)
    assert not thread.is_alive(), f"program did not finish within {SCHEDULER_TIMEOUT_SECONDS}s"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


@do
def child():
    yield Mark("child-effect")
    return 1


@do
def spawn_one_child():
    yield Mark("main-effect")
    task = yield Spawn(child())
    result = yield Wait(task)
    return result


def test_observer_inside_scheduler_sees_spawned_task_effects():
    """Case A of the issue: observer inside scheduled() must see child effects."""
    observed: list[str] = []
    program = scheduled(WithObserve(make_observer(observed), mark_handler(spawn_one_child())))

    result = run_with_timeout(program)

    assert result == 1
    assert "main-effect" in observed
    assert "child-effect" in observed, (
        f"Spawn dropped the WithObserve boundary: observed={observed}"
    )


def test_observer_outside_scheduler_sees_spawned_task_effects():
    """Case B of the issue (regression guard): outer observers keep working."""
    observed: list[str] = []
    program = WithObserve(make_observer(observed), scheduled(mark_handler(spawn_one_child())))

    result = run_with_timeout(program)

    assert result == 1
    assert observed == ["main-effect", "child-effect"]


def test_observer_inside_scheduler_sees_nested_spawn_effects():
    """Observer inheritance must recurse: grandchild effects are observed too."""

    @do
    def grandchild():
        yield Mark("grandchild-effect")
        return 2

    @do
    def middle_child():
        yield Mark("child-effect")
        task = yield Spawn(grandchild())
        result = yield Wait(task)
        return result

    @do
    def spawn_nested():
        yield Mark("main-effect")
        task = yield Spawn(middle_child())
        result = yield Wait(task)
        return result

    observed: list[str] = []
    program = scheduled(WithObserve(make_observer(observed), mark_handler(spawn_nested())))

    result = run_with_timeout(program)

    assert result == 2
    assert observed.count("main-effect") == 1
    assert "child-effect" in observed
    assert "grandchild-effect" in observed, (
        f"observer boundary lost across nested Spawn: observed={observed}"
    )


def test_observer_scoped_below_spawn_site_is_not_inherited():
    """Only boundaries between spawn site and scheduler are inherited.

    An observer installed around a sibling subtree (not enclosing the spawn
    site) must NOT see the spawned task's effects — inheritance follows the
    spawn site's dynamic scope, not global broadcast.
    """

    @do
    def sibling():
        yield Mark("sibling-effect")
        return None

    @do
    def spawn_outside_observer_scope():
        # Observer wraps only the sibling subtree; Spawn happens outside it.
        yield WithObserve(make_observer_sink, sibling())
        task = yield Spawn(child())
        result = yield Wait(task)
        return result

    observed_sibling: list[str] = []

    def make_observer_sink(effect):
        if isinstance(effect, Mark):
            observed_sibling.append(effect.label)

    program = scheduled(mark_handler(spawn_outside_observer_scope()))

    result = run_with_timeout(program)

    assert result == 1
    assert observed_sibling == ["sibling-effect"]


def test_cancelled_child_effects_before_cancel_are_observed():
    """Issue design point 3: the child owns its reinstalled boundary.

    Effects performed by the child before cancellation are observed; the
    cancel path releases the child's boundary without breaking the run.
    """

    @do
    def blocked_child(gate):
        yield Mark("pre-block-effect")
        yield Wait(gate.future)
        yield Mark("never-reached")
        return None

    @do
    def spawn_and_cancel():
        gate = yield CreatePromise()
        task = yield Spawn(blocked_child(gate))
        # Let the child run up to its Wait: spawn a helper and wait on it so
        # the scheduler gives the child a turn before we cancel.
        helper = yield Spawn(child())
        yield Wait(helper)
        yield Cancel(task)
        return "done"

    observed: list[str] = []
    program = scheduled(WithObserve(make_observer(observed), mark_handler(spawn_and_cancel())))

    result = run_with_timeout(program)

    assert result == "done"
    assert "pre-block-effect" in observed
    assert "never-reached" not in observed


def test_get_inner_observers_returns_observer_callables():
    """GetObservers(k) intrinsic: a handler can capture observer boundaries
    between the perform site and itself, symmetric to GetHandlers(k)."""
    from doeff.handler_utils import get_inner_observers

    @dataclasses.dataclass(frozen=True)
    class Probe(EffectBase):
        pass

    captured: list[Any] = []

    @handler
    @do
    def probe_handler(effect, k):
        if isinstance(effect, Probe):
            observers = yield get_inner_observers(k)
            captured.append(observers)
            return (yield Resume(k, None))
        return (yield Pass(effect, k))

    def my_observer(effect):
        return None

    @do
    def probing_program():
        yield Probe()
        return "ok"

    result = run(probe_handler(WithObserve(my_observer, probing_program())))

    assert result == "ok"
    assert len(captured) == 1
    observers = captured[0]
    assert len(observers) == 1
    assert observers[0] is my_observer
