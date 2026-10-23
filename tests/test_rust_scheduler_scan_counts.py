"""The Rust scheduler's work per step does not grow with the tasks parked elsewhere or the handler depth.

agora card ki-9de8d0a54408: the screen server's main thread spent 73% of its CPU inside the Rust VM,
about 3 ms per step of its loop. ki-1294af4fa525 took the full scans out of the Python scheduler only;
the Rust scheduler (the default) still walked the ready heap (has_live_ready_entry), every parked
waiter (keys_parked_by, detach_from_waiters) and every task row (live_semaphore_waiters) per step,
kept claimed external-wait placeholders in the ready heap until popped, and walked a detached
continuation chain with a linear find per fiber (DetachedFiberChain::fiber — O(L²) under L handlers).

The work is counted, not timed: ``doeff_vm.doeff_vm.vm_scan_counts()`` adds up the entries every scan
looked at, by what it walked.
"""

from __future__ import annotations

import statistics
import threading
from collections import Counter
from collections.abc import Callable, Generator
from dataclasses import dataclass

import pytest
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import (
    Cancel,
    CreateExternalPromise,
    Spawn,
    Task,
    Wait,
    _SchedulerIntrospection,
    scheduled,
)
from doeff_time import WaitWithin, async_time_handler
from doeff_vm.doeff_vm import vm_scan_counts

from doeff import EffectBase, K, Resume, do, handler, with_handlers
from doeff import run as doeff_run

pytestmark = [
    pytest.mark.usefixtures("scheduler_index_self_check"),
    # The parked deadline timers are cancelled before their sleep coroutine is awaited.
    pytest.mark.filterwarnings("ignore:coroutine .sleep. was never awaited:RuntimeWarning"),
]

# The terminal-entry sweep (#502) walks every row once per HANDLE_SWEEP_INTERVAL ids — amortized by
# design, so it is kept out of the per-step count.
_PER_STEP_SCANS = ("ready", "waiters", "tasks", "semaphore_waiters")


class Unrelated(EffectBase[int]):
    """An effect only the deep handler layers take: every scheduler effect passes them all."""


@handler
def unrelated_handler(effect: Unrelated, k: K) -> Generator[object, object, object]:
    return (yield Resume(k, 1))


@do
def noop():
    return 1
    yield  # a generator: @do takes a generator function


@do
def parked_timed():
    # turn-host's doorbell: a timed wait on a bell nobody rings (its deadline timer waits too).
    bell = yield CreateExternalPromise()
    yield WaitWithin(bell.future, 3600.0, park=True)


@do
def parked_plain():
    bell = yield CreateExternalPromise()
    yield Wait(bell.future)


@do
def timed_wait():
    # A CLI line already in: the future wins the race and the deadline timer is cancelled.
    bell = yield CreateExternalPromise()
    bell.complete(True)
    return (yield WaitWithin(bell.future, 3600.0, park=True))


@do
def blocking_wait():
    # A completion the scheduler blocks for: it pops the external-wait placeholder, finds no other
    # live work queued (has_live_ready_entry), checks the semaphore cycle (live_semaphore_waiters)
    # and blocks until the completion lands from another thread.
    bell = yield CreateExternalPromise()
    threading.Timer(0.002, bell.complete, (True,)).start()
    return (yield Wait(bell.future))


@do
def spawn_wait():
    return (yield Wait((yield Spawn(noop()))))


def _scans() -> Counter[str]:
    return Counter(vm_scan_counts())


@dataclass(frozen=True)
class OpScans:
    """The scans of one op: the median per-step scan entries, and the mean entries by what was scanned
    (``chain_fibers`` included)."""

    median: float
    by_scan: Counter[str]


def _scans_per_op(op: Callable[[], object], *, parked: int, layers: int, ops: int) -> OpScans:
    """The scans of one ``op`` while ``parked`` daemon tasks wait on external promises (half of them
    timed), under ``layers`` handlers between the body and the scheduler."""

    @do
    def body():
        tasks: tuple[Task[None], ...] = ()
        for i in range(parked):
            tasks = (*tasks, (yield Spawn(parked_timed() if i % 2 == 0 else parked_plain(), daemon=True)))
        for _ in range(4):  # the parked tasks start and park
            yield op()
        per_op: tuple[int, ...] = ()
        start = _scans()
        for _ in range(ops):
            before = _scans()
            yield op()
            after = _scans()
            per_op = (*per_op, sum(after[name] - before[name] for name in _PER_STEP_SCANS))
        total = _scans()
        total.subtract(start)
        for task in tasks:
            yield Cancel(task)
        return OpScans(
            median=statistics.median(per_op),
            by_scan=Counter({name: count / ops for name, count in total.items()}),
        )

    stack = [await_handler(), async_time_handler(), *[unrelated_handler] * layers]
    return doeff_run(scheduled(with_handlers(stack, body()), implementation="rust"))


class TestRustSchedulerStepDoesNotScanTheParkedTasks:
    """One wait's scheduler work must not grow with the tasks parked elsewhere (the Python version's
    TestTimedWaitWorkDoesNotGrowWithParkedTasks, for the Rust scheduler, counted by scans)."""

    PARKED_FEW = 16
    PARKED_MANY = 512
    OPS = 16

    @pytest.mark.parametrize(
        "op", [timed_wait, blocking_wait, spawn_wait], ids=["timed_wait", "blocking_wait", "spawn_wait"]
    )
    def test_one_step_does_not_scan_the_parked_tasks(self, op):
        few = _scans_per_op(op, parked=self.PARKED_FEW, layers=0, ops=self.OPS)
        many = _scans_per_op(op, parked=self.PARKED_MANY, layers=0, ops=self.OPS)
        grown = {
            name: round(many.by_scan[name] - few.by_scan[name])
            for name in _PER_STEP_SCANS
            if many.by_scan[name] > few.by_scan[name]
        }
        assert many.median <= few.median * 1.1 + 4, (
            f"one {op.__name__} scanned {few.median} scheduler entries with {self.PARKED_FEW} tasks "
            f"parked and {many.median} with {self.PARKED_MANY}; entries per op grown by scan: {grown}"
        )


def test_cancelled_deadline_timers_do_not_pile_up_in_the_rust_ready_heap():
    """A WaitWithin whose future wins cancels its deadline timer; the timer's external-wait
    placeholder (PRIORITY_EXTERNAL_WAIT) is then claimed, but stays in the ready heap until popped —
    and it is popped only when no NORMAL work is queued (the Python version's drop_claimed_marks,
    card ki-1294af4fa525). Here other NORMAL work stays queued for the whole run; the heap must not
    grow with the waits done meanwhile."""
    waits = 1000
    stop = [False]

    @do
    def busy():
        # Keeps NORMAL work queued until the waits are done.
        while not stop[0]:
            yield Wait((yield Spawn(noop())))

    @do
    def timed_wait_won_at_once():
        bell = yield CreateExternalPromise()
        bell.complete(True)  # drained before the race: the future wins at once
        return (yield WaitWithin(bell.future, 3600.0, park=True))

    @do
    def body():
        worker = yield Spawn(busy())
        for _ in range(waits):
            assert (yield timed_wait_won_at_once()) is True
        counts = yield _SchedulerIntrospection()
        stop[0] = True
        yield Wait(worker)
        return counts["ready"]

    ready = doeff_run(scheduled(
        with_handlers([await_handler(), async_time_handler()], body()),
        implementation="rust",
    ))
    assert ready < waits / 4, (
        f"the ready heap held {ready} entries after {waits} timed waits while other work stayed queued"
    )


def test_continuation_chain_walks_grow_linearly_with_the_handler_depth():
    """Every effect the scheduler takes passes the layers between the body and its prompt: each pass
    appends the next fiber to the continuation (set_tail_parent finds the chain's tail), and a Spawn
    walks the whole chain (boundary_callables, first_source_location). A walk that finds each fiber by
    a linear search is O(L²) under L handlers; the fibers looked at per layer must stay flat."""
    shallow, deep = 10, 80
    shallow_by_scan = _scans_per_op(spawn_wait, parked=0, layers=shallow, ops=8).by_scan
    deep_by_scan = _scans_per_op(spawn_wait, parked=0, layers=deep, ops=8).by_scan
    per_layer_shallow = shallow_by_scan["chain_fibers"] / shallow
    per_layer_deep = deep_by_scan["chain_fibers"] / deep
    assert per_layer_deep <= per_layer_shallow * 1.5, (
        f"one spawn_wait looked at {shallow_by_scan['chain_fibers']:.0f} chain fibers under {shallow} "
        f"handlers ({per_layer_shallow:.1f} per layer) and {deep_by_scan['chain_fibers']:.0f} under {deep} "
        f"({per_layer_deep:.1f} per layer)"
    )
