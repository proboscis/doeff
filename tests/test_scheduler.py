"""Tests for the cooperative scheduler."""

import pytest
from doeff import do, run, WithHandler, EffectBase, Resume, Pass
from doeff.scheduler import scheduler, Spawn, Gather, Wait, Task


@pytest.fixture
def sched():
    """Create a scheduler handler."""
    return scheduler()


# ---------------------------------------------------------------------------
# Spawn
# ---------------------------------------------------------------------------

class TestSpawn:
    def test_spawn_returns_task_handle(self, sched):
        """Spawn returns a Task handle."""
        @do
        def body():
            @do
            def child():
                return 42
            t = yield Spawn(child())
            assert isinstance(t, Task)
            return t.task_id

        result = run(WithHandler(sched, body()))
        assert isinstance(result, int)

    def test_spawn_task_runs(self, sched):
        """Spawned task actually executes."""
        ran = []

        @do
        def child():
            ran.append(True)
            return 1

        @do
        def body():
            t = yield Spawn(child())
            result = yield Wait(t)
            return result

        result = run(WithHandler(sched, body()))
        assert result == 1
        assert ran == [True]


# ---------------------------------------------------------------------------
# Wait
# ---------------------------------------------------------------------------

class TestWait:
    def test_wait_gets_result(self, sched):
        """Wait returns the task's result."""
        @do
        def child():
            return 42

        @do
        def body():
            t = yield Spawn(child())
            return (yield Wait(t))

        assert run(WithHandler(sched, body())) == 42

    def test_wait_multiple_tasks(self, sched):
        """Wait on multiple tasks sequentially."""
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

        assert run(WithHandler(sched, body())) == 30


# ---------------------------------------------------------------------------
# Gather
# ---------------------------------------------------------------------------

class TestGather:
    def test_gather_collects_results(self, sched):
        """Gather returns results in order."""
        @do
        def make_value(x):
            return x

        @do
        def body():
            t1 = yield Spawn(make_value(10))
            t2 = yield Spawn(make_value(20))
            t3 = yield Spawn(make_value(30))
            results = yield Gather(t1, t2, t3)
            return results

        assert run(WithHandler(sched, body())) == [10, 20, 30]

    def test_gather_with_effects(self, sched):
        """Tasks can use effects while being gathered."""
        class Ask(EffectBase):
            def __init__(self, key):
                super().__init__()
                self.key = key

        def ask_handler(effect, k):
            if isinstance(effect, Ask):
                result = yield Resume(k, f"val:{effect.key}")
                return result
            yield Pass(effect, k)

        @do
        def child(name):
            val = yield Ask(name)
            return val

        @do
        def body():
            t1 = yield Spawn(child("a"))
            t2 = yield Spawn(child("b"))
            return (yield Gather(t1, t2))

        result = run(WithHandler(ask_handler, WithHandler(sched, body())))
        assert result == ["val:a", "val:b"]
