"""A finished sim run lets go of what it built, so the next run in the same process starts clean.

A sim run ends with tasks still parked in the scheduler: the clock driver's final IDLE resume, and the
daemon tasks the root body never waited for. The Rust scheduler holds their continuations, and each
continuation holds its task's scheduler prompt, which holds the scheduler core back — a cycle of Rust
references the cycle collector cannot see. Before the root close-out let go of that state
(``SchedulerCore.end_run``), every such run kept its handler generators (the sim clock's runtime and
time queue, the parked tasks' frames and every value they reach) alive until the process ended: a
pytest session that ran the same world N times held N copies.
"""

import gc
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone

from doeff_core_effects import (
    CreatePromise,
    ReadVmLiveCounts,
    ReadVmWorkCounts,
    Spawn,
    scheduled,
    vm_probe_handler,
)
from doeff_time import Delay, WaitWithin, sim_time_handler

from doeff import do, run
from doeff import handler as program_handler

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Marker:
    """A value only the run's parked task holds — dead after the run once the run let go of the task."""


@dataclass(frozen=True)
class RunTrace:
    """What one run left behind: the steps it took, and weak references to what only the run held."""

    steps: int
    held: tuple[weakref.ref[object], ...]


def _read_steps() -> int:
    """The doeff-vm steps the process took so far (the reading run's own steps are the same for every read)."""
    steps, _handler_calls = run(vm_probe_handler(ReadVmWorkCounts()))
    return steps


@dataclass(frozen=True)
class _LiveCounts:
    """The VM's live segments, continuations and IR streams."""

    segments: int
    continuations: int
    ir_streams: int


def _live() -> _LiveCounts:
    """The VM's live segments, continuations and IR streams of the whole process."""
    return _LiveCounts(*run(vm_probe_handler(ReadVmLiveCounts())))


def _run_world() -> RunTrace:
    """Run a small sim world whose root returns while daemon tasks are parked on the clock and a promise."""

    @do
    def ticker():
        while True:
            yield Delay(3)

    @do
    def waiter(promise, marker: Marker):
        yield WaitWithin(promise.future, 1000.0)
        return marker

    @do
    def body():
        promise = yield CreatePromise()
        marker = Marker()
        yield Spawn(ticker(), daemon=True)
        yield Spawn(waiter(promise, marker), daemon=True)
        for _ in range(10):
            yield Delay(1)
        return (weakref.ref(promise), weakref.ref(marker))

    before = _read_steps()
    held = run(scheduled(program_handler(sim_time_handler(start_time=START))(body())))
    return RunTrace(steps=_read_steps() - before, held=held)


def test_a_finished_sim_run_leaves_nothing_alive_and_the_next_run_takes_the_same_steps() -> None:
    live_before = _live()

    first = _run_world()
    gc.collect()
    live_after_first = _live()
    second = _run_world()
    gc.collect()

    assert live_after_first == live_before, (
        "the first run left VM segments/continuations/streams alive"
    )
    assert _live() == live_before, "the second run left VM segments/continuations/streams alive"
    assert [ref() for ref in (*first.held, *second.held)] == [None] * 4, (
        "a parked task's promise or frame value outlived its run"
    )
    assert second.steps == first.steps, (first.steps, second.steps)
