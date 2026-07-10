"""Tests for the cooperative scheduler."""

import sys
import threading
from types import FrameType
from typing import Any

import pytest
from doeff_core_effects.scheduler import (
    PRIORITY_IDLE,
    AcquireSemaphore,
    Cancel,
    CompletePromise,
    CreateExternalPromise,
    CreatePromise,
    CreateSemaphore,
    Gather,
    Race,
    Spawn,
    Task,
    TaskCancelledError,
    Wait,
    scheduled,
)

from doeff import EffectBase, Pass, Resume, do
from doeff import handler as _install_raw_handler
from doeff import run as doeff_run

RACE_TIMEOUT_SECONDS = 2


def _run_race_with_timeout(program: Any) -> Any:
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            result["value"] = doeff_run(scheduled(program))
        except BaseException as exc:  # pragma: no cover - test helper
            error["value"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=RACE_TIMEOUT_SECONDS)
    assert not thread.is_alive(), f"Race did not complete within {RACE_TIMEOUT_SECONDS}s"

    if "value" in error:
        raise error["value"]

    return result["value"]


def _count_waitable_status_calls_for_gather(total: int) -> int:
    calls = 0

    @do
    def worker(gate: Any, value: int):
        _ = yield Wait(gate.future)
        return value

    @do
    def release_all(gates: list[Any]):
        for gate in gates:
            yield CompletePromise(gate, None)

    @do
    def body():
        gates: list[Any] = []
        tasks: list[Any] = []
        for value in range(total):
            gate = yield CreatePromise()
            gates.append(gate)
            tasks.append((yield Spawn(worker(gate, value))))
        _ = yield Spawn(release_all(gates), priority=PRIORITY_IDLE)
        return (yield Gather(*tasks))

    def profile_waitable_status(frame: FrameType, event: str, arg: object) -> Any:
        nonlocal calls
        if (
            event == "call"
            and frame.f_code.co_name == "waitable_status"
            and frame.f_code.co_filename.endswith("scheduler.py")
        ):
            calls += 1
        return profile_waitable_status

    previous_profile = sys.getprofile()
    sys.setprofile(profile_waitable_status)
    try:
        result = doeff_run(scheduled(body()))
    finally:
        sys.setprofile(previous_profile)

    assert result == list(range(total))
    return calls


def _run_scheduled_with_timeout(program: Any, timeout: float = 0.5) -> Any:
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def worker() -> None:
        try:
            result["value"] = doeff_run(scheduled(program))
        except BaseException as exc:  # pragma: no cover - helper re-raises in caller
            error["value"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    assert not thread.is_alive(), "scheduler blocked after cancelled external wait"

    if "value" in error:
        raise error["value"]

    return result["value"]


# ---------------------------------------------------------------------------
# Spawn + Wait
# ---------------------------------------------------------------------------

class TestSpawn:
    def test_spawn_returns_task_handle(self):
        """Spawn returns a Task handle."""
        @do
        def child():
            return 42

        @do
        def body():
            t = yield Spawn(child())
            assert isinstance(t, Task)
            return t.task_id

        result = doeff_run(scheduled(body()))
        assert isinstance(result, int)

    def test_spawn_and_wait(self):
        """Spawned task runs and Wait gets result."""
        @do
        def child():
            return 42

        @do
        def body():
            t = yield Spawn(child())
            return (yield Wait(t))

        assert doeff_run(scheduled(body())) == 42

    def test_wait_multiple_sequential(self):
        """Wait on tasks sequentially."""
        @do
        def make_value(x):
            return x * 10

        @do
        def body():
            t1 = yield Spawn(make_value(1))
            t2 = yield Spawn(make_value(2))
            r1 = yield Wait(t1)
            r2 = yield Wait(t2)
            return r1 + r2

        assert doeff_run(scheduled(body())) == 30


# ---------------------------------------------------------------------------
# Gather
# ---------------------------------------------------------------------------

class TestGather:
    def test_gather_collects_results(self):
        """Gather returns results in order."""
        @do
        def make_value(x):
            return x

        @do
        def body():
            t1 = yield Spawn(make_value(10))
            t2 = yield Spawn(make_value(20))
            t3 = yield Spawn(make_value(30))
            return (yield Gather(t1, t2, t3))

        assert doeff_run(scheduled(body())) == [10, 20, 30]

    def test_gather_wake_waiter_status_checks_are_linear(self):
        """Gather wake-up work should scale linearly with task count."""
        small_total = 200
        large_total = 400

        small_calls = _count_waitable_status_calls_for_gather(small_total)
        large_calls = _count_waitable_status_calls_for_gather(large_total)

        assert small_calls <= small_total * 20
        assert large_calls <= large_total * 20
        assert large_calls <= small_calls * 3

    def test_gather_wakes_on_first_failed_pending_task(self):
        """Gather raises when any pending child fails, not only the first pending child."""
        events: list[str] = []

        @do
        def blocked(gate: Any):
            _ = yield Wait(gate.future)
            events.append("blocked released")
            return "blocked"

        @do
        def failing(gate: Any):
            _ = yield Wait(gate.future)
            events.append("failing raised")
            raise RuntimeError("boom")
            yield

        @do
        def release_failure_then_blocker(failure_gate: Any, blocker_gate: Any):
            events.append("release failure")
            yield CompletePromise(failure_gate, None)
            events.append("release blocker")
            yield CompletePromise(blocker_gate, None)

        @do
        def body():
            blocker_gate = yield CreatePromise()
            failure_gate = yield CreatePromise()
            blocker_task = yield Spawn(blocked(blocker_gate))
            failing_task = yield Spawn(failing(failure_gate))
            _ = yield Spawn(
                release_failure_then_blocker(failure_gate, blocker_gate),
                priority=PRIORITY_IDLE,
            )
            try:
                yield Gather(blocker_task, failing_task)
                return "should not reach"
            except RuntimeError as error:
                events.append(f"caught:{error}")
                return list(events)

        assert doeff_run(scheduled(body())) == [
            "release failure",
            "failing raised",
            "caught:boom",
        ]

    def test_gather_cancelled_child_raises_task_cancelled_error(self):
        @do
        def blocked(gate: Any):
            return (yield Wait(gate.future))

        @do
        def body():
            gate = yield CreatePromise()
            task = yield Spawn(blocked(gate))
            yield Cancel(task)
            try:
                yield Gather(task)
                return "should not reach"
            except TaskCancelledError:
                return "cancelled"

        assert doeff_run(scheduled(body())) == "cancelled"


# ---------------------------------------------------------------------------
# Race
# ---------------------------------------------------------------------------

class TestRace:
    def test_race_wakes_when_second_pending_task_completes(self):
        """Race wakes on the winning pending task even when it is not first."""
        @do
        def blocked(gate: Any):
            return (yield Wait(gate.future))

        @do
        def worker(gate: Any, value: str):
            _ = yield Wait(gate.future)
            return value

        @do
        def release_fast(gate: Any):
            yield CompletePromise(gate, None)

        @do
        def body():
            blocked_gate = yield CreatePromise()
            fast_gate = yield CreatePromise()
            blocked_task = yield Spawn(blocked(blocked_gate))
            fast_task = yield Spawn(worker(fast_gate, "fast"))
            _ = yield Spawn(release_fast(fast_gate), priority=PRIORITY_IDLE)
            return (yield Race(blocked_task, fast_task))

        assert _run_race_with_timeout(body()) == "fast"

    def test_race_wakes_when_third_pending_task_completes(self):
        @do
        def blocked(gate: Any):
            return (yield Wait(gate.future))

        @do
        def worker(gate: Any, value: str):
            _ = yield Wait(gate.future)
            return value

        @do
        def release_winner(gate: Any):
            yield CompletePromise(gate, None)

        @do
        def body():
            first_gate = yield CreatePromise()
            second_gate = yield CreatePromise()
            third_gate = yield CreatePromise()
            first_task = yield Spawn(blocked(first_gate))
            second_task = yield Spawn(blocked(second_gate))
            third_task = yield Spawn(worker(third_gate, "third"))
            _ = yield Spawn(release_winner(third_gate), priority=PRIORITY_IDLE)
            return (yield Race(first_task, second_task, third_task))

        assert _run_race_with_timeout(body()) == "third"

    def test_race_raises_when_pending_winner_fails(self):
        @do
        def blocked(gate: Any):
            return (yield Wait(gate.future))

        @do
        def failing(gate: Any):
            _ = yield Wait(gate.future)
            raise RuntimeError("race failure")
            yield

        @do
        def release_failure(gate: Any):
            yield CompletePromise(gate, None)

        @do
        def body():
            blocked_gate = yield CreatePromise()
            failure_gate = yield CreatePromise()
            blocked_task = yield Spawn(blocked(blocked_gate))
            failing_task = yield Spawn(failing(failure_gate))
            _ = yield Spawn(release_failure(failure_gate), priority=PRIORITY_IDLE)
            try:
                yield Race(blocked_task, failing_task)
                return "should not reach"
            except RuntimeError as error:
                return str(error)

        assert _run_race_with_timeout(body()) == "race failure"

    def test_race_raises_when_pending_winner_is_cancelled(self):
        @do
        def blocked(gate: Any):
            return (yield Wait(gate.future))

        @do
        def cancel_task(task: Any):
            yield Cancel(task)

        @do
        def body():
            blocked_gate = yield CreatePromise()
            cancelled_gate = yield CreatePromise()
            blocked_task = yield Spawn(blocked(blocked_gate))
            cancelled_task = yield Spawn(blocked(cancelled_gate))
            _ = yield Spawn(cancel_task(cancelled_task), priority=PRIORITY_IDLE)
            try:
                yield Race(blocked_task, cancelled_task)
                return "should not reach"
            except TaskCancelledError:
                return "cancelled"

        assert _run_race_with_timeout(body()) == "cancelled"

    def test_race_duplicate_waitable_resolves_once(self):
        @do
        def worker(gate: Any):
            _ = yield Wait(gate.future)
            return "duplicate"

        @do
        def release_gate(gate: Any):
            yield CompletePromise(gate, None)

        @do
        def body():
            gate = yield CreatePromise()
            task = yield Spawn(worker(gate))
            _ = yield Spawn(release_gate(gate), priority=PRIORITY_IDLE)
            return (yield Race(task, task))

        assert _run_race_with_timeout(body()) == "duplicate"

    def test_race_resolves_once_when_waitables_complete_back_to_back(self):
        events: list[str] = []

        @do
        def complete_both(first: Any, second: Any):
            first.complete("first")
            second.complete("second")

        @do
        def body():
            first = yield CreateExternalPromise()
            second = yield CreateExternalPromise()
            _ = yield Spawn(complete_both(first, second), priority=PRIORITY_IDLE)
            winner = yield Race(first.future, second.future)
            events.append(f"winner:{winner}")
            return list(events)

        assert _run_race_with_timeout(body()) == ["winner:first"]


# ---------------------------------------------------------------------------
# Stress + Concurrency
# ---------------------------------------------------------------------------

class TestPriority:
    def test_high_priority_runs_first(self):
        """Higher priority task runs before lower priority."""
        from doeff_core_effects.scheduler import PRIORITY_HIGH, PRIORITY_IDLE

        order = []

        @do
        def task_low():
            order.append("low")
            return "low"

        @do
        def task_high():
            order.append("high")
            return "high"

        @do
        def body():
            # Spawn low first, then high
            t_low = yield Spawn(task_low(), priority=PRIORITY_IDLE)
            t_high = yield Spawn(task_high(), priority=PRIORITY_HIGH)
            return (yield Gather(t_low, t_high))

        result = doeff_run(scheduled(body()))
        assert result == ["low", "high"]
        # High priority should have run first
        assert order[0] == "high"

    def test_same_priority_fifo(self):
        """Same priority tasks run in FIFO order."""
        order = []

        @do
        def task(name):
            order.append(name)
            return name

        @do
        def body():
            t1 = yield Spawn(task("first"))
            t2 = yield Spawn(task("second"))
            t3 = yield Spawn(task("third"))
            return (yield Gather(t1, t2, t3))

        result = doeff_run(scheduled(body()))
        assert result == ["first", "second", "third"]
        assert order == ["first", "second", "third"]


class TestStress:
    def test_100_tasks(self):
        """Spawn and gather 100 tasks."""
        @do
        def make_value(x):
            return x

        @do
        def body():
            tasks = []
            for i in range(100):
                t = yield Spawn(make_value(i))
                tasks.append(t)
            return (yield Gather(*tasks))

        results = doeff_run(scheduled(body()))
        assert results == list(range(100))

    def test_nested_spawn(self):
        """Task spawns sub-tasks."""
        @do
        def leaf(x):
            return x * 10

        @do
        def parent_task(base):
            t1 = yield Spawn(leaf(base))
            t2 = yield Spawn(leaf(base + 1))
            r1 = yield Wait(t1)
            r2 = yield Wait(t2)
            return r1 + r2

        @do
        def body():
            t = yield Spawn(parent_task(5))
            return (yield Wait(t))

        assert doeff_run(scheduled(body())) == 110  # 50 + 60


class TestConcurrency:
    def test_interleaved_effects(self):
        """Tasks with effects interleave via the scheduler."""
        log = []

        class Log(EffectBase):
            def __init__(self, msg):
                super().__init__()
                self.msg = msg

        @do
        def log_handler(effect, k):
            if isinstance(effect, Log):
                log.append(effect.msg)
                result = yield Resume(k, None)
                return result
            yield Pass(effect, k)

        @do
        def task_a():
            yield Log("a1")
            yield Log("a2")
            return "A"

        @do
        def task_b():
            yield Log("b1")
            yield Log("b2")
            return "B"

        @do
        def body():
            ta = yield Spawn(task_a())
            tb = yield Spawn(task_b())
            results = yield Gather(ta, tb)
            return results

        result = doeff_run(_install_raw_handler(log_handler)(scheduled(body())))
        assert result == ["A", "B"]
        # Both tasks ran — all log entries present
        assert "a1" in log
        assert "a2" in log
        assert "b1" in log
        assert "b2" in log

    def test_gather_waits_for_all(self):
        """Gather blocks until all tasks complete."""
        order = []

        @do
        def task_a():
            order.append("a_start")
            order.append("a_end")
            return 1

        @do
        def task_b():
            order.append("b_start")
            order.append("b_end")
            return 2

        @do
        def body():
            ta = yield Spawn(task_a())
            tb = yield Spawn(task_b())
            results = yield Gather(ta, tb)
            order.append("gathered")
            return results

        result = doeff_run(scheduled(body()))
        assert result == [1, 2]
        assert order[-1] == "gathered"
        # Both tasks completed before gather
        assert "a_end" in order
        assert "b_end" in order

    def test_wait_on_already_completed(self):
        """Wait on a task that already finished returns immediately."""
        @do
        def fast():
            return 99

        @do
        def slow():
            return 1

        @do
        def body():
            t_fast = yield Spawn(fast())
            t_slow = yield Spawn(slow())
            # Wait on slow first (forces fast to complete while waiting)
            r_slow = yield Wait(t_slow)
            # Fast is already done
            r_fast = yield Wait(t_fast)
            return (r_fast, r_slow)

        assert doeff_run(scheduled(body())) == (99, 1)

    def test_many_spawns_then_gather(self):
        """Spawn many tasks, gather all at once."""
        @do
        def compute(x):
            return x * x

        @do
        def body():
            tasks = []
            for i in range(50):
                tasks.append((yield Spawn(compute(i))))
            results = yield Gather(*tasks)
            return sum(results)

        result = doeff_run(scheduled(body()))
        assert result == sum(i * i for i in range(50))


# ---------------------------------------------------------------------------
# Promise
# ---------------------------------------------------------------------------

class TestPromise:
    def test_create_and_complete_promise(self):
        """Internal promise: create, complete, wait."""
        from doeff_core_effects.scheduler import CompletePromise, CreatePromise

        @do
        def body():
            p = yield CreatePromise()
            yield CompletePromise(p, 42)
            return (yield Wait(p.future))

        assert doeff_run(scheduled(body())) == 42

    def test_promise_across_tasks(self):
        """One task creates promise, another waits, first completes."""
        from doeff_core_effects.scheduler import CompletePromise, CreatePromise

        @do
        def body():
            p = yield CreatePromise()
            # Spawn a task that waits on the promise
            @do
            def waiter():
                return (yield Wait(p.future))
            tw = yield Spawn(waiter())
            # Complete the promise
            yield CompletePromise(p, 99)
            # Wait for the waiter to finish
            return (yield Wait(tw))

        assert doeff_run(scheduled(body())) == 99


# ---------------------------------------------------------------------------
# ExternalPromise
# ---------------------------------------------------------------------------

class TestExternalPromise:
    def test_external_promise_complete(self):
        """ExternalPromise: complete from outside, task gets value."""
        from doeff_core_effects.scheduler import CreateExternalPromise

        @do
        def body():
            ep = yield CreateExternalPromise()
            # Simulate external completion (would normally come from another thread)
            ep.complete(77)
            # Wait for it
            return (yield Wait(ep.future))

        assert doeff_run(scheduled(body())) == 77

    def test_external_promise_with_spawned_task(self):
        """Spawned task waits on external promise, main completes it."""
        from doeff_core_effects.scheduler import CreateExternalPromise

        @do
        def body():
            ep = yield CreateExternalPromise()

            @do
            def waiter():
                return (yield Wait(ep.future))

            tw = yield Spawn(waiter())
            # Complete externally
            ep.complete("external_value")
            return (yield Wait(tw))

        assert doeff_run(scheduled(body())) == "external_value"

    def test_100_threads_concurrent(self):
        """100 tasks each sleeping 0.1s in threads. Must finish in <2s, not 10s."""
        import threading
        import time

        from doeff_core_effects.scheduler import CreateExternalPromise

        @do
        def sleep_task(i):
            ep = yield CreateExternalPromise()

            def worker():
                time.sleep(0.1)
                ep.complete(i)

            threading.Thread(target=worker, daemon=True).start()
            return (yield Wait(ep.future))

        @do
        def body():
            tasks = []
            for i in range(100):
                tasks.append((yield Spawn(sleep_task(i))))
            return (yield Gather(*tasks))

        start = time.time()
        results = doeff_run(scheduled(body()))
        elapsed = time.time() - start

        assert results == list(range(100))
        assert elapsed < 2.0, f"took {elapsed:.1f}s — not concurrent!"

    def test_resolved_external_wait_not_stalled_behind_unresolved_peer(self):
        """Regression for #490: a resolved external wait resumes promptly.

        Two slow promises complete at T+0.6s; the fast one at T+0.05s. Spawn
        order fixes the ready-heap FIFO to [slow, slow, fast]: with the bug,
        fast's completion is drained during slow-1's blocking get without
        waking fast, then the next pop (slow-2, unresolved) blocks the loop
        until the slow completions arrive — fast stalls ~0.55s despite being
        resolved. The waiters registration must wake it the moment its
        completion is drained.
        """
        import time

        from doeff_core_effects.scheduler import CreateExternalPromise

        t_fast_completed: list[float] = [0.0]
        t_fast_resumed: list[float] = [0.0]

        def complete_later(ep: Any, delay: float, mark: list[float] | None = None) -> None:
            def worker() -> None:
                time.sleep(delay)
                if mark is not None:
                    mark[0] = time.perf_counter()
                ep.complete("done")

            threading.Thread(target=worker, daemon=True).start()

        @do
        def slow_task():
            ep = yield CreateExternalPromise()
            complete_later(ep, 0.6)
            return (yield Wait(ep.future))

        @do
        def fast_task():
            ep = yield CreateExternalPromise()
            complete_later(ep, 0.05, mark=t_fast_completed)
            value = yield Wait(ep.future)
            t_fast_resumed[0] = time.perf_counter()
            return value

        @do
        def body():
            tasks = [(yield Spawn(slow_task())), (yield Spawn(slow_task()))]
            tasks.append((yield Spawn(fast_task())))
            return (yield Gather(*tasks))

        results = doeff_run(scheduled(body()))

        assert results == ["done", "done", "done"]
        stall = t_fast_resumed[0] - t_fast_completed[0]
        assert stall < 0.25, (
            f"fast external wait stalled {stall * 1000:.0f}ms behind an "
            f"unresolved peer (#490)"
        )


# ---------------------------------------------------------------------------
# Priority survives suspension (#493 / #504)
# ---------------------------------------------------------------------------

class TestPrioritySurvivesSuspension:
    """Regressions for #493/#504: wake paths must respect stored task priority."""

    def test_completer_not_demoted_behind_pending_external_wait(self):
        """Regression for #493: CompletePromise re-queues the completer at its
        own task priority, not unconditionally at IDLE.

        With the IDLE demotion, the completer's continuation sat below the
        PRIORITY_EXTERNAL_WAIT placeholder of the pending 1.5s Await, whose
        pick_next branch blocks the scheduler thread in _drain_one_external —
        the completer froze for the full Await duration (~1.5s) despite being
        runnable immediately after CompletePromise.
        """
        import asyncio
        import time

        from doeff_core_effects.effects import Await
        from doeff_core_effects.handlers import await_handler

        timeline: dict[str, float] = {}
        t0 = time.perf_counter()

        @do
        def long_await():
            yield Await(asyncio.sleep(1.5))

        @do
        def listener(fut):
            return (yield Wait(fut))

        @do
        def completer(p):
            timeline["complete_performed"] = time.perf_counter() - t0
            yield CompletePromise(p, 42)
            timeline["completer_resumed"] = time.perf_counter() - t0

        @do
        def body():
            p = yield CreatePromise()
            tasks = [
                (yield Spawn(listener(p.future))),
                (yield Spawn(long_await())),
                (yield Spawn(completer(p))),
            ]
            yield Gather(*tasks)

        doeff_run(scheduled(await_handler()(body())))

        stall = timeline["completer_resumed"] - timeline["complete_performed"]
        assert stall < 0.5, (
            f"completer stalled {stall * 1000:.0f}ms behind a pending "
            f"external wait after CompletePromise (#493)"
        )

    def test_high_priority_task_woken_before_normal_backlog(self):
        """Regression for #504: a HIGH task woken after a park is served
        before NORMAL entries already queued in the ready heap.

        Before the fix, every wake re-enqueued at the enqueue_resume default
        (NORMAL), so a woken HIGH task went behind the NORMAL backlog (FIFO
        within the same priority) and the HIGH spawner was demoted to NORMAL
        after each Spawn.
        """
        from doeff_core_effects.scheduler import PRIORITY_HIGH

        order: list[str] = []

        @do
        def high_waiter(fut):
            yield Wait(fut)
            order.append("high")

        @do
        def normal_task(name):
            order.append(name)

        @do
        def coordinator(gate):
            # Runs at HIGH so the NORMAL spawns below stay queued (backlog)
            # until the gate completes.
            t_high = yield Spawn(high_waiter(gate.future), priority=PRIORITY_HIGH)
            t_n1 = yield Spawn(normal_task("n1"))
            t_n2 = yield Spawn(normal_task("n2"))
            yield CompletePromise(gate, None)
            return [t_high, t_n1, t_n2]

        @do
        def body():
            gate = yield CreatePromise()
            t_coord = yield Spawn(coordinator(gate), priority=PRIORITY_HIGH)
            tasks = yield Wait(t_coord)
            yield Gather(*tasks)
            return order

        result = doeff_run(scheduled(body()))
        assert result == ["high", "n1", "n2"], (
            f"woken HIGH task was not served before queued NORMAL backlog: {result}"
        )

    def test_idle_spawner_not_promoted_above_external_wait_shield(self):
        """Regression for #504: an IDLE task performing Spawn resumes at IDLE,
        below the PRIORITY_EXTERNAL_WAIT shield — not at a hard-coded NORMAL
        above it.

        Before the fix, the spawner resume promoted an IDLE spawner past the
        shield for one step, so its continuation ran ahead of a pending
        non-IDLE external wait instead of being held back like every other
        IDLE entry.
        """
        import time

        timeline: dict[str, float] = {}
        t0 = time.perf_counter()

        @do
        def noop():
            return None

        @do
        def external_waiter(parked_fut, blocking_fut):
            # Fully parked (no ready-heap placeholder) until idle_spawner
            # completes parked_fut; then holds the EXTERNAL_WAIT shield
            # while blocking_fut is pending.
            yield Wait(parked_fut, priority=PRIORITY_IDLE)
            return (yield Wait(blocking_fut))

        @do
        def idle_spawner(parked_ep):
            parked_ep.complete(None)  # plain queue put, drained at next step
            yield Spawn(noop())
            timeline["idle_resumed"] = time.perf_counter() - t0

        @do
        def body():
            blocking_ep = yield CreateExternalPromise()
            parked_ep = yield CreateExternalPromise()
            t_w = yield Spawn(external_waiter(parked_ep.future, blocking_ep.future))
            t_i = yield Spawn(idle_spawner(parked_ep), priority=PRIORITY_IDLE)

            def complete_later():
                time.sleep(0.3)
                timeline["external_completed"] = time.perf_counter() - t0
                blocking_ep.complete("done")

            threading.Thread(target=complete_later, daemon=True).start()
            yield Gather(t_w, t_i)
            return timeline

        result = doeff_run(scheduled(body()))
        assert result["idle_resumed"] >= result["external_completed"], (
            "IDLE spawner resumed at "
            f"+{result['idle_resumed'] * 1000:.0f}ms, ahead of the pending "
            "external wait (completed at "
            f"+{result['external_completed'] * 1000:.0f}ms) — Spawn promoted "
            "an IDLE task above PRIORITY_EXTERNAL_WAIT (#504)"
        )


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

class TestCancel:
    def test_cancel_blocked_task(self):
        """Cancel a task that's blocked waiting on a promise."""
        from doeff_core_effects.scheduler import Cancel, CreateExternalPromise, TaskCancelledError

        @do
        def body():
            ep = yield CreateExternalPromise()

            @do
            def blocked_task():
                return (yield Wait(ep.future))  # blocks forever

            t = yield Spawn(blocked_task())
            yield Cancel(t)
            try:
                yield Wait(t)
                return "should not reach"
            except TaskCancelledError:
                return "cancelled"

        assert doeff_run(scheduled(body())) == "cancelled"

    def test_cancelled_promise_waiter_does_not_resume_when_promise_completes(self):
        """Cancelled tasks parked on a promise must not resume later."""
        events: list[str] = []

        @do
        def blocked_task(gate: Any):
            events.append("waiting")
            _ = yield Wait(gate.future)
            events.append("resumed")
            return "resumed"

        @do
        def body():
            gate = yield CreatePromise()
            task = yield Spawn(blocked_task(gate))

            yield Cancel(task)
            yield CompletePromise(gate, "late")

            try:
                yield Wait(task)
                return ("completed", list(events))
            except TaskCancelledError:
                return ("cancelled", list(events))

        assert doeff_run(scheduled(body())) == ("cancelled", ["waiting"])

    def test_cancelled_external_waiter_does_not_block_scheduler(self):
        """A cancelled external wait entry must not block later scheduler progress."""
        events: list[str] = []

        @do
        def blocked_task(external_promise: Any):
            events.append("waiting")
            _ = yield Wait(external_promise.future)
            events.append("resumed")
            return "resumed"

        @do
        def release_gate(gate: Any):
            yield CompletePromise(gate, "released")

        @do
        def body():
            external_promise = yield CreateExternalPromise()
            gate = yield CreatePromise()
            task = yield Spawn(blocked_task(external_promise))

            yield Cancel(task)
            _ = yield Spawn(release_gate(gate), priority=PRIORITY_IDLE)
            gate_value = yield Wait(gate.future)

            try:
                yield Wait(task)
                return ("completed", gate_value, list(events))
            except TaskCancelledError:
                return ("cancelled", gate_value, list(events))

        assert _run_scheduled_with_timeout(body()) == ("cancelled", "released", ["waiting"])

    def test_cancel_does_not_affect_completed(self):
        """Cancelling an already-completed task is a no-op."""
        from doeff_core_effects.scheduler import Cancel

        @do
        def fast():
            return 99

        @do
        def body():
            t = yield Spawn(fast())
            r = yield Wait(t)
            yield Cancel(t)  # no-op
            return r

        assert doeff_run(scheduled(body())) == 99


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------

class TestErrorPropagation:
    def test_wait_raises_on_failed_task(self):
        """Wait on a failed task raises the error."""
        @do
        def failing():
            raise ValueError("boom")
            yield  # make it a generator

        @do
        def body():
            t = yield Spawn(failing())
            try:
                yield Wait(t)
                return "should not reach"
            except ValueError as e:
                return str(e)

        assert doeff_run(scheduled(body())) == "boom"

    def test_gather_fail_fast(self):
        """Gather raises on first failed task."""
        @do
        def good():
            return 1

        @do
        def bad():
            raise RuntimeError("fail")
            yield

        @do
        def body():
            t1 = yield Spawn(good())
            t2 = yield Spawn(bad())
            try:
                yield Gather(t1, t2)
                return "should not reach"
            except RuntimeError as e:
                return str(e)

        assert doeff_run(scheduled(body())) == "fail"

    def test_gather_all_succeed(self):
        """Gather with no failures returns all results."""
        @do
        def make(x):
            return x

        @do
        def body():
            t1 = yield Spawn(make(1))
            t2 = yield Spawn(make(2))
            t3 = yield Spawn(make(3))
            return (yield Gather(t1, t2, t3))

        assert doeff_run(scheduled(body())) == [1, 2, 3]


# ---------------------------------------------------------------------------
# Semaphore
# ---------------------------------------------------------------------------

class TestSemaphore:
    def test_binary_semaphore_mutual_exclusion(self):
        """Binary semaphore (permits=1) ensures mutual exclusion."""
        from doeff_core_effects.scheduler import AcquireSemaphore, CreateSemaphore, ReleaseSemaphore

        log = []

        @do
        def critical_section(sem, name):
            yield AcquireSemaphore(sem)
            log.append(f"{name}_enter")
            log.append(f"{name}_exit")
            yield ReleaseSemaphore(sem)
            return name

        @do
        def body():
            sem = yield CreateSemaphore(1)
            t1 = yield Spawn(critical_section(sem, "A"))
            t2 = yield Spawn(critical_section(sem, "B"))
            return (yield Gather(t1, t2))

        result = doeff_run(scheduled(body()))
        assert result == ["A", "B"]
        # Mutual exclusion: no interleaving of enter/exit
        # Either [A_enter, A_exit, B_enter, B_exit] or [B_enter, B_exit, A_enter, A_exit]
        assert (log in (["A_enter", "A_exit", "B_enter", "B_exit"], ["B_enter", "B_exit", "A_enter", "A_exit"]))

    def test_counting_semaphore(self):
        """Counting semaphore allows N concurrent accessors."""
        from doeff_core_effects.scheduler import AcquireSemaphore, CreateSemaphore, ReleaseSemaphore

        @do
        def worker(sem, i):
            yield AcquireSemaphore(sem)
            result = i * 10
            yield ReleaseSemaphore(sem)
            return result

        @do
        def body():
            sem = yield CreateSemaphore(3)  # allow 3 concurrent
            tasks = []
            for i in range(10):
                tasks.append((yield Spawn(worker(sem, i))))
            return (yield Gather(*tasks))

        result = doeff_run(scheduled(body()))
        assert result == [i * 10 for i in range(10)]

    def test_release_too_many_raises(self):
        """Releasing more than max permits raises error."""
        from doeff_core_effects.scheduler import CreateSemaphore, ReleaseSemaphore

        @do
        def body():
            sem = yield CreateSemaphore(1)
            try:
                yield ReleaseSemaphore(sem)  # no acquire — over-release
                return "should not reach"
            except RuntimeError as e:
                return str(e)

        assert "too many" in doeff_run(scheduled(body()))

    def test_semaphore_only_deadlock_raises_scheduler_deadlock_error(self):
        """Semaphore waiters must keep the scheduler from silently returning None."""
        from doeff_core_effects.scheduler import SchedulerDeadlockError

        @do
        def waiter(sem: Any):
            yield AcquireSemaphore(sem)
            return "acquired"

        @do
        def body():
            sem = yield CreateSemaphore(1)
            yield AcquireSemaphore(sem)
            _ = yield Spawn(waiter(sem))
            _ = yield Spawn(waiter(sem))
            yield AcquireSemaphore(sem)
            return "should not reach"

        with pytest.raises(SchedulerDeadlockError) as exc_info:
            doeff_run(scheduled(body()))

        error = exc_info.value
        assert error.semaphore_waiters == {0: [1, 2]}
        assert "semaphore 0" in str(error)
        assert "tasks 1, 2" in str(error)
