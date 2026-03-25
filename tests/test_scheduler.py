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
