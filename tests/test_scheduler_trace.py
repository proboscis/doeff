"""The scheduler's measurement hook ``set_scheduler_trace`` (agora-redesign #3855 / #3861).

- The CPU a task used is the sum, over its steps, of the thread CPU time from
  the event that put the task on the thread (``task-enter``, or its own
  previous ``task-leave`` when the handler resumed it directly) to its next
  ``task-leave``. A task that hardly computes stays near zero even while a
  busy task runs between its steps; the plain "thread CPU at the start of the
  span minus at its end" would bill it the busy task's work.
- Without a sink the scheduler emits nothing.
- Every external completion is accounted for: each ``external-complete`` is
  matched by one ``external-drained`` or ``external-ignored``.
- Only the Python scheduler emits events; the Rust scheduler refuses to run
  while a sink is installed instead of measuring nothing.

These use the real thread clock, so the computation is kept to a few ms.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from doeff_core_effects.scheduler import (
    CompletePromise,
    CreateExternalPromise,
    CreatePromise,
    Spawn,
    Wait,
    scheduled,
    set_scheduler_trace,
)

from doeff import do, run

STEPS = 6
BUSY_CPU_MS = 4


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


def test_task_cpu_bills_only_the_task_that_computed(events: list[dict[str, Any]]) -> None:
    busy_tid, idle_tid = run(scheduled(_busy_and_idle_ping_pong(), implementation="python"))
    by_task = _cpu_by_task(events)
    busy_ns, idle_ns = by_task[busy_tid], by_task[idle_tid]
    assert busy_ns >= STEPS * BUSY_CPU_MS * 1_000_000
    assert idle_ns < busy_ns / 10, (busy_ns, idle_ns)
    # The comparison: the plain span of the idle task covers the busy task's steps between its
    # own, so counted that way it would fail the same bound.
    assert _cpu_by_span(events, idle_tid) >= busy_ns / 10, (busy_ns, _cpu_by_span(events, idle_tid))


def test_spawn_names_parent_and_child_and_a_step_carries_its_vm_steps(events: list[dict[str, Any]]) -> None:
    """agora-redesign #4188: a task tree can be rebuilt from the events (``spawned`` names the parent and the
    child), and each ``task-leave`` carries the doeff-vm steps and handler calls of the step it closes."""
    busy_tid, idle_tid = run(scheduled(_busy_and_idle_ping_pong(), implementation="python"))
    spawned = {e["tid"]: e["parent"] for e in events if e["event"] == "spawned"}
    root_tid = spawned[busy_tid]
    assert spawned[idle_tid] == root_tid and root_tid not in (busy_tid, idle_tid), spawned
    leaves = [e for e in events if e["event"] == "task-leave" and e["step_ns"] is not None]
    assert leaves and all(e["step_vm_steps"] >= 0 and e["step_handler_calls"] >= 0 for e in leaves), leaves[:3]
    assert any(e["step_vm_steps"] > 0 for e in leaves if e["tid"] == busy_tid)


def test_no_sink_emits_no_events(events: list[dict[str, Any]]) -> None:
    set_scheduler_trace(None)
    run(scheduled(_busy_and_idle_ping_pong(), implementation="python"))
    assert events == []


def test_each_external_completion_is_drained_or_ignored(events: list[dict[str, Any]]) -> None:
    @do
    def body():
        promise = yield CreateExternalPromise()

        def complete_twice() -> None:
            promise.complete("first")
            promise.complete("second")

        thread = threading.Thread(target=complete_twice)
        thread.start()
        value = yield Wait(promise.future)
        thread.join()
        return value

    assert run(scheduled(body(), implementation="python")) == "first"
    counts = {
        name: sum(1 for e in events if e["event"] == name)
        for name in ("external-complete", "external-drained", "external-ignored")
    }
    assert counts == {"external-complete": 2, "external-drained": 1, "external-ignored": 1}


def test_rust_scheduler_refuses_a_sink(events: list[dict[str, Any]]) -> None:
    with pytest.raises(RuntimeError, match="set_scheduler_trace"):
        scheduled(_busy_and_idle_ping_pong(), implementation="rust")
