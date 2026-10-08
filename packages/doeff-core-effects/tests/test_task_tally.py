"""The task tally (agora-redesign #4188 — U6 of #3855): OpenTaskTally / ReadTaskTally / CloseTaskTally answered by
step-tally-handler.

- The table has one row per task that took a step while the window was open, keyed by (run, tid), with the
  task's parent (from the ``spawned`` event) and its steps' wall / CPU ns and doeff-vm steps / handler calls.
- Summing a task tree through the parent links bills each tree only the CPU its own tasks used: a tree that
  hardly computes stays under a tenth of a busy tree even while the busy tree runs between its steps.
- The doeff-vm steps per task do not depend on how busy the machine is: the same program run twice gives the
  same steps for every task.
- ReadTaskTally answers the table so far and keeps the window open; CloseTaskTally answers it and closes.
- Step windows and task windows share the scheduler's one trace sink; the sink is removed with the last window.

These use the real thread clock, so the computation is kept to a few ms.
"""

from __future__ import annotations

import time
from typing import Any

from doeff_core_effects.scheduler import Spawn, Wait, scheduled, scheduler_trace_sink
from doeff_core_effects.scheduler_step_tally import step_tally_handler
from doeff_core_effects.step_tally_effects import (
    CloseStepTally,
    CloseTaskTally,
    OpenStepTally,
    OpenTaskTally,
    ReadTaskTally,
    TaskTally,
)

from doeff import do, run, with_handlers

BUSY_CPU_MS = 4
STEPS = 3


def _burn_cpu(ms: float) -> None:
    """Spend ``ms`` of this thread's CPU time (not wall time: a busy machine must not shrink it)."""
    end = time.thread_time_ns() + int(ms * 1_000_000)
    while time.thread_time_ns() < end:
        pass


def _child(burn: bool):
    @do
    def child():
        if burn:
            _burn_cpu(BUSY_CPU_MS)
        return None
        yield  # a generator: @do takes a generator function

    return child()


def _tree(burn: bool):
    """A task that spawns one child per step and waits for it; when ``burn``, the task and its children compute."""

    @do
    def parent():
        for _ in range(STEPS):
            if burn:
                _burn_cpu(BUSY_CPU_MS)
            task = yield Spawn(_child(burn))
            yield Wait(task)

    return parent()


@do
def _two_trees():
    busy = yield Spawn(_tree(True))
    idle = yield Spawn(_tree(False))
    yield Wait(busy)
    yield Wait(idle)
    return busy.task_id, idle.task_id


def _tallied(body):
    return run(scheduled(with_handlers([step_tally_handler], body), implementation="python"))


def _tree_rows(table: tuple[TaskTally, ...], root: int) -> list[TaskTally]:
    """The rows of ``root`` and every task below it (parent links within the same run)."""
    run_id = next(row.run for row in table if row.tid == root)
    members = {root}
    grew = True
    while grew:
        grew = False
        for row in table:
            if row.run == run_id and row.parent in members and row.tid not in members:
                members.add(row.tid)
                grew = True
    return [row for row in table if row.run == run_id and row.tid in members]


@do
def _tallied_trees():
    yield OpenTaskTally("trees")
    busy_tid, idle_tid = yield _two_trees()
    table = yield CloseTaskTally("trees")
    return busy_tid, idle_tid, table


def test_a_tree_that_hardly_computes_is_billed_only_its_own_cpu() -> None:
    busy_tid, idle_tid, table = _tallied(_tallied_trees())
    assert isinstance(table, tuple) and all(isinstance(row, TaskTally) for row in table), table
    busy_tree, idle_tree = _tree_rows(table, busy_tid), _tree_rows(table, idle_tid)
    # each tree is its root and its STEPS children
    assert len(busy_tree) == STEPS + 1 and len(idle_tree) == STEPS + 1, (busy_tree, idle_tree)
    busy_cpu = sum(row.cpu_ns for row in busy_tree)
    idle_cpu = sum(row.cpu_ns for row in idle_tree)
    # the busy tree's sum includes its children's CPU, not just its root's
    busy_root_cpu = next(row.cpu_ns for row in busy_tree if row.tid == busy_tid)
    assert busy_cpu >= 2 * STEPS * BUSY_CPU_MS * 1_000_000, busy_tree
    assert busy_cpu - busy_root_cpu >= STEPS * BUSY_CPU_MS * 1_000_000, busy_tree
    assert idle_cpu < busy_cpu / 10, (busy_cpu, idle_cpu)
    assert scheduler_trace_sink() is None


def test_the_same_program_takes_the_same_vm_steps_per_task() -> None:
    _tallied(_tallied_trees())  # warm: first-time imports must not count against either compared run
    _, _, first = _tallied(_tallied_trees())
    _, _, second = _tallied(_tallied_trees())

    def steps_by_tid(table: tuple[TaskTally, ...]) -> dict[Any, tuple[int, int]]:
        return {row.tid: (row.vm_steps, row.handler_calls) for row in table}

    assert steps_by_tid(first) == steps_by_tid(second)
    assert all(vm_steps > 0 for vm_steps, _ in steps_by_tid(first).values()), first


def test_read_keeps_the_window_open_and_close_answers_none_for_an_unknown_key() -> None:
    @do
    def body():
        yield OpenTaskTally("open")
        task = yield Spawn(_child(False))
        yield Wait(task)
        so_far = yield ReadTaskTally("open")
        still_open = scheduler_trace_sink() is not None
        task = yield Spawn(_child(False))
        yield Wait(task)
        closed = yield CloseTaskTally("open")
        never = yield CloseTaskTally("never")
        return so_far, still_open, closed, never

    so_far, still_open, closed, never = _tallied(body())
    assert still_open
    assert len(closed) > len(so_far) > 0, (so_far, closed)
    assert never is None
    assert scheduler_trace_sink() is None


def test_step_and_task_windows_share_the_one_sink() -> None:
    @do
    def body():
        yield OpenStepTally("steps")
        yield OpenTaskTally("tasks")
        yield _two_trees()
        steps = yield CloseStepTally("steps")
        sink_after_step_close = scheduler_trace_sink()
        tasks = yield CloseTaskTally("tasks")
        return steps, sink_after_step_close, tasks

    steps, sink_after_step_close, tasks = _tallied(body())
    assert steps.steps > 0 and len(tasks) > 0, (steps, tasks)
    assert sink_after_step_close is not None  # the task window still needed it
    assert scheduler_trace_sink() is None
