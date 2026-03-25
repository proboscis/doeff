"""Tests for the cooperative scheduler."""

from doeff import do, run as doeff_run, EffectBase, Resume, Pass
from doeff.scheduler import scheduled, Spawn, Gather, Wait, Task


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


# ---------------------------------------------------------------------------
# Stress + Concurrency
# ---------------------------------------------------------------------------

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

        from doeff.program import WithHandler
        result = doeff_run(WithHandler(log_handler, scheduled(body())))
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
        from doeff.scheduler import CreatePromise, CompletePromise

        @do
        def body():
            p = yield CreatePromise()
            yield CompletePromise(p, 42)
            return (yield Wait(p.future))

        assert doeff_run(scheduled(body())) == 42

    def test_promise_across_tasks(self):
        """One task creates promise, another waits, first completes."""
        from doeff.scheduler import CreatePromise, CompletePromise

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
        from doeff.scheduler import CreateExternalPromise

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
        from doeff.scheduler import CreateExternalPromise

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
        from doeff.scheduler import CreateExternalPromise

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


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

class TestCancel:
    def test_cancel_blocked_task(self):
        """Cancel a task that's blocked waiting on a promise."""
        from doeff.scheduler import Cancel, TaskCancelledError, CreateExternalPromise

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

    def test_cancel_does_not_affect_completed(self):
        """Cancelling an already-completed task is a no-op."""
        from doeff.scheduler import Cancel

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
        from doeff.scheduler import CreateSemaphore, AcquireSemaphore, ReleaseSemaphore

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
        assert (log == ["A_enter", "A_exit", "B_enter", "B_exit"] or
                log == ["B_enter", "B_exit", "A_enter", "A_exit"])

    def test_counting_semaphore(self):
        """Counting semaphore allows N concurrent accessors."""
        from doeff.scheduler import CreateSemaphore, AcquireSemaphore, ReleaseSemaphore

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
        from doeff.scheduler import CreateSemaphore, ReleaseSemaphore

        @do
        def body():
            sem = yield CreateSemaphore(1)
            try:
                yield ReleaseSemaphore(sem)  # no acquire — over-release
                return "should not reach"
            except RuntimeError as e:
                return str(e)

        assert "too many" in doeff_run(scheduled(body()))
