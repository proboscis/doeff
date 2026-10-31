"""The scheduler's measurement hook ``set_scheduler_trace``.

- The CPU a task used is the sum, over its steps, of the thread CPU time from
  the event that put the task on the thread (``task-enter``, or its own
  previous ``task-leave`` when the handler resumed it directly) to its next
  ``task-leave``. A task that hardly computes stays near zero even while a
  busy task runs between its steps; the plain "thread CPU at the start of the
  span minus at its end" would bill it the busy task's work.
- Without a sink the scheduler emits nothing.
- Every external completion the scheduler takes out of its queue is accounted
  for: each ``external-complete`` taken out before the run ends is matched by
  one ``external-drained`` or ``external-ignored``. A completion still queued
  when the root returns is never taken out, so the test waits for one queued
  after it (the queue is FIFO) instead of racing the root's return.
- Both versions emit the events — the default Rust scheduler too: the same
  kinds with the same fields in the same order (the clock values and the
  doeff-vm counts differ), including the ``task-leave`` of an effect the
  scheduler only passes on to an outer handler, and a sink installed after
  the scheduler was built or in the middle of a run.

These use the real thread clock, so the computation is kept to a few ms.
"""

from __future__ import annotations

import contextlib
import itertools
import sys
import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from typing import Any, Generic, TypeVar

import pytest
from doeff_core_effects import (
    AcquireSemaphore,
    Cancel,
    CompletePromise,
    CreateExternalPromise,
    CreatePromise,
    CreateSemaphore,
    Gather,
    Get,
    Put,
    Race,
    ReleaseSemaphore,
    Spawn,
    TaskCancelledError,
    TaskPriority,
    Wait,
    scheduled,
    state,
)
from doeff_core_effects._scheduler import set_scheduler_trace

from doeff import do, run, with_handlers

pytestmark = pytest.mark.usefixtures("scheduler_index_self_check")

IMPLEMENTATIONS = ("python", "rust")
STEPS = 6
BUSY_CPU_MS = 4
# The fields whose values are clocks, doeff-vm counts or the run number: they differ between two runs.
_MEASURED_FIELDS = frozenset(
    ("ns", "cpu_ns", "step_ns", "step_cpu_ns", "step_vm_steps", "step_handler_calls", "run")
)


@pytest.fixture
def events() -> Iterator[list[dict[str, Any]]]:
    collected: list[dict[str, Any]] = []
    set_scheduler_trace(collected.append)
    try:
        yield collected
    finally:
        set_scheduler_trace(None)


def _burn_cpu(ms: float) -> None:
    """Spend ``ms`` of this thread's CPU time (not wall time: a busy machine must not shrink it)."""
    end = time.thread_time_ns() + int(ms * 1_000_000)
    while time.thread_time_ns() < end:
        pass


def _cpu_by_task(events: list[dict[str, Any]]) -> dict[Any, int]:
    """Bill the CPU before each ``task-leave`` to the leaving task, when the event before it is
    that same task's ``task-enter`` or ``task-leave`` (the scheduler picking the next task in
    between is billed to nobody)."""
    steps = [e for e in events if e["event"] in ("task-enter", "task-leave")]
    billed = [
        (cur["tid"], cur["cpu_ns"] - prev["cpu_ns"])
        for prev, cur in zip(steps, steps[1:], strict=False)
        if cur["event"] == "task-leave" and prev["tid"] == cur["tid"]
    ]
    return {tid: sum(ns for owner, ns in billed if owner == tid) for tid, _ in billed}


def _cpu_by_span(events: list[dict[str, Any]], tid: Any) -> int:
    """The plain form: thread CPU at the task's last ``task-leave`` minus at its first ``task-enter``."""
    enters = [e["cpu_ns"] for e in events if e["event"] == "task-enter" and e["tid"] == tid]
    leaves = [e["cpu_ns"] for e in events if e["event"] == "task-leave" and e["tid"] == tid]
    return leaves[-1] - enters[0]


def _comparable(events: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    """The events with what differs between two runs left out: the measured values, and of a step only whether it
    was measured (``step_ns`` is None when the step began before this sink)."""
    return [
        {
            **{name: value for name, value in event.items() if name not in _MEASURED_FIELDS},
            **({"measured": event["step_ns"] is not None} if "step_ns" in event else {}),
        }
        for event in events
    ]


@do
def _promises(count: int):
    promises: tuple[Any, ...] = ()
    for _ in range(count):
        promises = (*promises, (yield CreatePromise()))
    return promises


@do
def _busy_and_idle_ping_pong():
    """Two tasks take turns: ``busy`` computes a few ms per step, ``idle`` only hands the turn back."""
    pings = yield _promises(STEPS)
    pongs = yield _promises(STEPS)

    @do
    def busy():
        for ping, pong in zip(pings, pongs, strict=True):
            _burn_cpu(BUSY_CPU_MS)
            yield CompletePromise(ping, None)
            yield Wait(pong.future)

    @do
    def idle():
        for ping, pong in zip(pings, pongs, strict=True):
            yield Wait(ping.future)
            yield CompletePromise(pong, None)

    busy_task = yield Spawn(busy())
    idle_task = yield Spawn(idle())
    yield Wait(busy_task)
    yield Wait(idle_task)
    return busy_task.task_id, idle_task.task_id


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_task_cpu_bills_only_the_task_that_computed(events: list[dict[str, Any]], implementation: str) -> None:
    busy_tid, idle_tid = run(scheduled(_busy_and_idle_ping_pong(), implementation=implementation))
    by_task = _cpu_by_task(events)
    busy_ns, idle_ns = by_task[busy_tid], by_task[idle_tid]
    assert busy_ns >= STEPS * BUSY_CPU_MS * 1_000_000
    assert idle_ns < busy_ns / 10, (busy_ns, idle_ns)
    # The comparison: the plain span of the idle task covers the busy task's steps between its
    # own, so counted that way it would fail the same bound.
    assert _cpu_by_span(events, idle_tid) >= busy_ns / 10, (busy_ns, _cpu_by_span(events, idle_tid))


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_spawn_names_parent_and_child_and_a_step_carries_its_vm_steps(
    events: list[dict[str, Any]], implementation: str
) -> None:
    """A task tree can be rebuilt from the events (``spawned`` names the parent and the
    child), and each ``task-leave`` carries the doeff-vm steps and handler calls of the step it closes."""
    busy_tid, idle_tid = run(scheduled(_busy_and_idle_ping_pong(), implementation=implementation))
    spawned = {e["tid"]: e["parent"] for e in events if e["event"] == "spawned"}
    root_tid = spawned[busy_tid]
    assert spawned[idle_tid] == root_tid and root_tid not in (busy_tid, idle_tid), spawned
    leaves = [e for e in events if e["event"] == "task-leave" and e["step_ns"] is not None]
    assert leaves and all(e["step_vm_steps"] >= 0 and e["step_handler_calls"] >= 0 for e in leaves), leaves[:3]
    assert any(e["step_vm_steps"] > 0 for e in leaves if e["tid"] == busy_tid)


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_no_sink_emits_no_events(events: list[dict[str, Any]], implementation: str) -> None:
    set_scheduler_trace(None)
    run(scheduled(_busy_and_idle_ping_pong(), implementation=implementation))
    assert events == []


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_each_external_completion_is_drained_or_ignored(events: list[dict[str, Any]], implementation: str) -> None:
    @do
    def body():
        promise = yield CreateExternalPromise()
        settled = yield CreateExternalPromise()
        resumed = threading.Event()

        def complete_twice() -> None:
            promise.complete("first")
            # The worst order: the second completion lands only after the body is back from Wait.
            resumed.wait()
            promise.complete("second")
            # The completion queue is one FIFO, so the scheduler takes "second" out before this one.
            settled.complete(None)

        thread = threading.Thread(target=complete_twice)
        thread.start()
        value = yield Wait(promise.future)
        resumed.set()
        yield Wait(settled.future)
        thread.join()
        return value, promise.promise_id

    value, pid = run(scheduled(body(), implementation=implementation))
    assert value == "first"
    counts = {
        name: sum(1 for e in events if e["event"] == name and e["pid"] == pid)
        for name in ("external-complete", "external-drained", "external-ignored")
    }
    assert counts == {"external-complete": 2, "external-drained": 1, "external-ignored": 1}


# --- both versions emit the same events ---------------------------------------------------------------------


@do
def _external_in_run():
    """In-run external completions: one wakes a waiter (an external wake queued behind other work), one settles a
    Race, and a second completion of the first promise is ignored."""
    first = yield CreateExternalPromise()
    second = yield CreateExternalPromise()

    @do
    def completer():
        first.complete("x")
        first.complete("again")
        second.complete("y")
        return None
        yield  # a generator: @do takes a generator function

    @do
    def waiter():
        return (yield Wait(first.future))

    waiting = yield Spawn(waiter())
    completing = yield Spawn(completer())
    raced = yield Race(waiting, second.future)
    yield Wait(completing)
    yield Wait(waiting)
    return raced


@do
def _semaphores_cancel_and_failure():
    """Semaphore hand-offs, effects an outer handler answers (Get / Put pass through the scheduler), a failing
    task, a Cancel of a parked task and a Gather."""
    semaphore = yield CreateSemaphore(1)

    @do
    def worker(index: int):
        yield AcquireSemaphore(semaphore)
        total = yield Get("n")
        yield Put("n", total + index)
        yield ReleaseSemaphore(semaphore)
        return index

    @do
    def failing():
        yield Get("n")
        raise ValueError("boom")

    @do
    def parked():
        promise = yield CreatePromise()
        yield Wait(promise.future)

    workers: tuple[object, ...] = ()
    for index in range(3):
        workers = (*workers, (yield Spawn(worker(index))))
    parked_task = yield Spawn(parked())
    failed = yield Spawn(failing())
    with contextlib.suppress(ValueError):
        yield Wait(failed)
    yield Cancel(parked_task)
    with contextlib.suppress(TaskCancelledError):
        yield Wait(parked_task)
    return (yield Gather(*workers))


@do
def _idle_daemon_behind_an_external_wait():
    """An idle daemon behind an external wait, completed in-run between two passed effects — the Python handler drains
    the completion at the passed effect, before the next scheduler effect."""
    promise = yield CreateExternalPromise()

    @do
    def ticking():
        for _ in range(3):
            yield Get("n")

    @do
    def completer():
        yield Get("n")
        promise.complete(5)
        yield Get("n")
        yield Put("n", 1)

    yield Spawn(ticking(), daemon=True, priority=TaskPriority.IDLE)
    completing = yield Spawn(completer())
    value = yield Wait(promise.future)
    yield Wait(completing)
    return value



_SCENARIOS = {
    "ping_pong": _busy_and_idle_ping_pong,
    "external_in_run": _external_in_run,
    "semaphores_cancel_and_failure": _semaphores_cancel_and_failure,
    "idle_daemon_behind_an_external_wait": _idle_daemon_behind_an_external_wait,
}

_T = TypeVar("_T")


class _Recorder(Generic[_T]):
    """A sink (or an unraisable hook) that keeps, in order, what it is given — to read what one run emitted."""

    def __init__(self) -> None:
        self.seen: tuple[_T, ...] = ()

    def __call__(self, item: _T) -> None:
        self.seen = (*self.seen, item)  # noqa: DOEFF002 — 検の補助が受けた項目を貯める欄


def _traced(program: object, implementation: str) -> tuple[object, tuple[dict[str, object], ...]]:  # noqa: DOEFF006 — 検の補助の戻り
    """Run ``program`` under one version with a sink installed, with Get / Put answered outside the scheduler, and
    answer its result and events."""
    recorded: _Recorder[dict[str, object]] = _Recorder()
    set_scheduler_trace(recorded)
    try:
        result = run(with_handlers([state({"n": 0})], scheduled(program, implementation=implementation)))
    finally:
        set_scheduler_trace(None)
    return result, recorded.seen


@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
def test_both_versions_emit_the_same_events(scenario: str) -> None:
    python_result, python_events = _traced(_SCENARIOS[scenario](), "python")
    rust_result, rust_events = _traced(_SCENARIOS[scenario](), "rust")
    assert rust_result == python_result
    assert python_events, scenario
    assert _comparable(rust_events) == _comparable(python_events)


def test_an_effect_passed_to_an_outer_handler_ends_a_step_in_both_versions() -> None:
    """The Python handler sees every effect at its prompt, so a Get answered outside the scheduler ends the task's
    step; the Rust scheduler, which declines the Get, emits the same task-leave and drains at it."""
    _, events = _traced(_idle_daemon_behind_an_external_wait(), "rust")
    leaves = [e["effect"] for e in events if e["event"] == "task-leave"]
    assert "Get" in leaves, leaves
    assert "Put" in leaves, leaves
    drained_after = [prev["effect"] for prev, cur in itertools.pairwise(events) if cur["event"] == "external-drained"]
    assert drained_after == ["Get"], drained_after


def _installing_mid_run(sink: _Recorder[dict[str, object]]):
    """A body whose spawned task installs ``sink`` from its own code, between two effects."""

    @do
    def child():
        yield Get("n")
        set_scheduler_trace(sink)
        yield Get("n")
        promise = yield CreatePromise()
        return promise.promise_id

    @do
    def body():
        task = yield Spawn(child())
        yield Get("n")
        answer = yield Wait(task)
        yield Get("n")
        return answer

    return body()


def test_a_sink_installed_in_the_middle_of_a_run_is_followed() -> None:
    """A sink installed by a task's own code: the step that began before it is not measured, the next ones are —
    the same events in both versions."""

    def mid_run(implementation: str) -> tuple[dict[str, object], ...]:
        recorded: _Recorder[dict[str, object]] = _Recorder()
        try:
            run(with_handlers([state({"n": 0})], scheduled(_installing_mid_run(recorded), implementation=implementation)))
        finally:
            set_scheduler_trace(None)
        return recorded.seen

    python_events, rust_events = mid_run("python"), mid_run("rust")
    assert rust_events, rust_events
    first = rust_events[0]
    assert first["event"] == "task-leave", rust_events
    assert first["step_ns"] is None, first
    assert any(e.get("step_ns") is not None for e in rust_events), rust_events
    assert _comparable(rust_events) == _comparable(python_events)


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_a_sink_installed_after_the_scheduler_was_built_is_followed(implementation: str) -> None:
    """The 2026-10-17 probe: a Rust scheduler built before the sink was installed emitted nothing. Now the run's
    events come under either version."""
    program = scheduled(_busy_and_idle_ping_pong(), implementation=implementation)
    recorded: _Recorder[dict[str, object]] = _Recorder()
    set_scheduler_trace(recorded)
    try:
        run(program)
    finally:
        set_scheduler_trace(None)
    kinds = {e["event"] for e in recorded.seen}
    assert {"spawned", "enqueued", "resumed", "task-enter", "task-leave"} <= kinds, kinds
    assert "start" not in kinds  # the run started (scheduled() was called) before the sink


def test_default_scheduler_emits_the_python_scheduler_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default is the Rust scheduler, and a plain ``scheduled`` under a sink emits what the Python one does."""
    monkeypatch.delenv("DOEFF_SCHEDULER", raising=False)

    def traced(implementation: str | None) -> tuple[dict[str, object], ...]:
        recorded: _Recorder[dict[str, object]] = _Recorder()
        set_scheduler_trace(recorded)
        try:
            run(scheduled(_busy_and_idle_ping_pong(), implementation=implementation))
        finally:
            set_scheduler_trace(None)
        return recorded.seen

    default_events, python_events = traced(None), traced("python")
    assert any(e["event"] == "task-leave" for e in default_events)
    assert _comparable(default_events) == _comparable(python_events)


def test_python_scheduler_chosen_by_environment_emits_events(
    events: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOEFF_SCHEDULER", "python")
    run(scheduled(_busy_and_idle_ping_pong()))
    assert any(e["event"] == "task-leave" for e in events)


# --- a sink that raises -------------------------------------------------------------------------------------


def test_rust_scheduler_reports_an_exception_of_the_sink_and_keeps_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """An event site of the Rust scheduler sits inside a state change: an Exception the sink raises there goes to
    sys.unraisablehook, and the run's answer is unchanged."""
    reported: _Recorder[object] = _Recorder()
    monkeypatch.setattr(sys, "unraisablehook", lambda unraisable: reported(unraisable.exc_value))

    def failing_sink(event: dict[str, object]) -> None:
        if event["event"] in ("enqueued", "task-leave"):
            raise RuntimeError(f"sink failed at {event['event']}")

    set_scheduler_trace(failing_sink)
    try:
        answer = run(scheduled(_busy_and_idle_ping_pong(), implementation="rust"))
    finally:
        set_scheduler_trace(None)
    assert answer == run(scheduled(_busy_and_idle_ping_pong(), implementation="rust"))
    assert reported.seen, reported.seen
    assert all(isinstance(error, RuntimeError) for error in reported.seen), reported.seen[:3]


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
# The interrupted run's close-out names the work it abandons (#501) — expected here.
@pytest.mark.filterwarnings("ignore:scheduler root body returned:RuntimeWarning")
def test_a_keyboard_interrupt_of_the_sink_unwinds_out_of_the_run(implementation: str) -> None:
    """A KeyboardInterrupt that lands in the sink is not swallowed: the Rust scheduler throws it at the next
    resumed task, and it unwinds out of the run as it does under the Python scheduler."""
    interrupted: _Recorder[str] = _Recorder()

    def interrupting_sink(event: dict[str, object]) -> None:
        if event["event"] == "enqueued" and not interrupted.seen:
            interrupted("enqueued")
            raise KeyboardInterrupt

    set_scheduler_trace(interrupting_sink)
    try:
        with pytest.raises(KeyboardInterrupt):
            run(scheduled(_busy_and_idle_ping_pong(), implementation=implementation))
    finally:
        set_scheduler_trace(None)
    assert interrupted.seen == ("enqueued",)
