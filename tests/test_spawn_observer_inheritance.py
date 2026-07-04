"""Spawn must inherit WithObserve (observer) boundaries like handlers.

Issue: scheduler-spawn-drops-observer-boundary

Before this fix, Spawn captured inner *handlers* from the spawn-site
continuation (get_inner_handlers) and reinstalled them on the child task,
but observer boundaries (WithObserve) between the spawn site and the
scheduler were dropped: observers inside scheduled() never saw effects
performed by spawned tasks (silent drop — fail-fast violation for
observer-based instrumentation such as OpenTelemetry spans).

The fix captures the full interleaved boundary stack (handlers AND
observers, innermost-first) via get_inner_boundaries / GetBoundaries(k)
and reinstalls both kinds on the child, preserving their relative
nesting order.
"""
import dataclasses

from doeff_core_effects import Gather, Spawn, Wait, scheduled

from doeff import EffectBase, Pass, Resume, WithObserve, do, handler, run


@dataclasses.dataclass(frozen=True)
class Mark(EffectBase):
    label: str


@dataclasses.dataclass(frozen=True)
class HandlerNoise(EffectBase):
    """Effect emitted from a handler *body* (not from user code)."""

    label: str


@handler
@do
def mark_handler(effect, k):
    if isinstance(effect, Mark):
        return (yield Resume(k, None))
    return (yield Pass(effect, k))


@handler
@do
def noisy_mark_handler(effect, k):
    """Handles Mark and emits HandlerNoise from its own body."""
    if isinstance(effect, Mark):
        yield HandlerNoise(f"noise:{effect.label}")
        return (yield Resume(k, None))
    return (yield Pass(effect, k))


@handler
@do
def noise_sink_handler(effect, k):
    if isinstance(effect, HandlerNoise):
        return (yield Resume(k, None))
    return (yield Pass(effect, k))


def make_observer(sink):
    def observer(effect):
        if isinstance(effect, (Mark, HandlerNoise)):
            sink.append((type(effect).__name__, effect.label))

    return observer


def observed_labels(sink, effect_name):
    return [label for name, label in sink if name == effect_name]


@do
def child():
    yield Mark("child-effect")
    return 1


@do
def spawning_main():
    yield Mark("main-effect")
    task = yield Spawn(child())
    result = yield Wait(task)
    return result


def test_observer_inside_scheduler_sees_spawned_child_effects():
    """Repro case A from the issue: observer inside scheduled()."""
    seen = []
    program = scheduled(WithObserve(make_observer(seen), mark_handler(spawning_main())))
    result = run(program)
    assert result == 1
    marks = observed_labels(seen, "Mark")
    assert "main-effect" in marks
    assert "child-effect" in marks, (
        f"Spawn dropped the WithObserve boundary — observed only {marks}"
    )


def test_observer_outside_scheduler_still_sees_all():
    """Repro case B from the issue (worked before the fix): regression guard."""
    seen = []
    program = WithObserve(make_observer(seen), scheduled(mark_handler(spawning_main())))
    result = run(program)
    assert result == 1
    marks = observed_labels(seen, "Mark")
    assert "main-effect" in marks
    assert "child-effect" in marks


def test_spawned_observer_does_not_see_handler_internal_effects():
    """Inheritance preserves the observer's nesting relative to handlers.

    Original nesting: noise_sink(noisy_mark(WithObserve(obs, main))) — the
    observer sits BELOW noisy_mark_handler, so effects performed by that
    handler's own body (HandlerNoise) are dispatched above the observer
    boundary and must NOT be observed. The spawned child must reproduce the
    same nesting: obs sees the child's Mark but not the handler's noise.
    A flat "observers outermost" reinstall would over-observe here.
    """
    seen = []
    program = scheduled(
        noise_sink_handler(
            noisy_mark_handler(WithObserve(make_observer(seen), spawning_main()))
        )
    )
    result = run(program)
    assert result == 1
    marks = observed_labels(seen, "Mark")
    assert "main-effect" in marks
    assert "child-effect" in marks
    noise = observed_labels(seen, "HandlerNoise")
    assert noise == [], (
        f"Observer must keep its position below noisy_mark_handler in the "
        f"spawned child; it observed handler-internal effects: {noise}"
    )


@do
def labeled_child(label):
    yield Mark(label)
    return label


@do
def gather_main():
    task_1 = yield Spawn(labeled_child("child-1"))
    task_2 = yield Spawn(labeled_child("child-2"))
    results = yield Gather(task_1, task_2)
    return list(results)


def test_gather_multiple_children_all_observed():
    seen = []
    program = scheduled(WithObserve(make_observer(seen), mark_handler(gather_main())))
    result = run(program)
    assert result == ["child-1", "child-2"]
    marks = observed_labels(seen, "Mark")
    assert "child-1" in marks
    assert "child-2" in marks


@do
def grandchild():
    yield Mark("grandchild-effect")
    return 2


@do
def spawning_child():
    yield Mark("child-effect")
    task = yield Spawn(grandchild())
    result = yield Wait(task)
    return result


@do
def nested_spawn_main():
    task = yield Spawn(spawning_child())
    result = yield Wait(task)
    return result


def test_nested_spawn_inherits_observer_transitively():
    seen = []
    program = scheduled(
        WithObserve(make_observer(seen), mark_handler(nested_spawn_main()))
    )
    result = run(program)
    assert result == 2
    marks = observed_labels(seen, "Mark")
    assert "child-effect" in marks
    assert "grandchild-effect" in marks


# ---------------------------------------------------------------------------
# Primitive: get_inner_boundaries / GetBoundaries(k)
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Probe(EffectBase):
    pass


def make_probe_handler(captured):
    from doeff.handler_utils import get_inner_boundaries

    @handler
    @do
    def probe_handler(effect, k):
        if isinstance(effect, Probe):
            boundaries = yield get_inner_boundaries(k)
            captured.append(boundaries)
            return (yield Resume(k, None))
        return (yield Pass(effect, k))

    return probe_handler


def test_get_inner_boundaries_shape():
    """get_inner_boundaries returns the interleaved boundary stack
    (innermost-first), tagged by kind, excluding the calling handler."""
    captured = []
    seen = []

    @do
    def probing_body():
        yield Probe()
        return "done"

    program = make_probe_handler(captured)(
        mark_handler(WithObserve(make_observer(seen), probing_body()))
    )
    result = run(program)
    assert result == "done"
    assert len(captured) == 1
    boundaries = captured[0]
    kinds = [kind for kind, _ in boundaries]
    assert kinds == ["observer", "handler"], f"got {kinds}"
    assert all(callable(entry) for _, entry in boundaries)
