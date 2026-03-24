"""Tests for the cooperative scheduler."""

import pytest
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
