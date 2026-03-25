"""Tests for error propagation — Python exceptions flow through the VM correctly."""

import pytest
from doeff import do, run as doeff_run, WithHandler
from doeff.program import Resume, Pass, WithHandler
from doeff_vm import EffectBase
from doeff_core_effects.scheduler import scheduled, Spawn, Wait, Gather, TaskCancelledError


class TestDirectErrors:
    def test_body_raises_value_error(self):
        """ValueError in body propagates as ValueError."""
        @do
        def body():
            raise ValueError("test error")
            yield  # make generator

        with pytest.raises(ValueError, match="test error"):
            doeff_run(body())

    def test_body_raises_runtime_error(self):
        @do
        def body():
            raise RuntimeError("boom")
            yield

        with pytest.raises(RuntimeError, match="boom"):
            doeff_run(body())

    def test_body_raises_key_error(self):
        @do
        def body():
            d = {}
            _ = d["missing"]
            yield

        with pytest.raises(KeyError):
            doeff_run(body())

    def test_body_raises_type_error(self):
        @do
        def body():
            _ = 1 + "string"
            yield

        with pytest.raises(TypeError):
            doeff_run(body())


class TestHandlerErrors:
    def test_handler_raises_propagates(self):
        """Exception raised in handler body propagates."""
        class Eff(EffectBase):
            def __init__(self):
                super().__init__()

        @do
        def handler(effect, k):
            raise ValueError("handler error")
            yield Resume(k, None)

        @do
        def body():
            yield Eff()

        with pytest.raises(ValueError, match="handler error"):
            doeff_run(WithHandler(handler, body()))


class TestTaskErrors:
    def test_wait_propagates_task_error(self):
        """Wait on a failed task raises the task's error."""
        @do
        def failing_task():
            raise ValueError("task failed")
            yield

        @do
        def body():
            t = yield Spawn(failing_task())
            return (yield Wait(t))

        with pytest.raises(ValueError, match="task failed"):
            doeff_run(scheduled(body()))

    def test_gather_propagates_first_error(self):
        """Gather fail-fast: first error propagates."""
        @do
        def good():
            return 1

        @do
        def bad():
            raise RuntimeError("gather fail")
            yield

        @do
        def body():
            t1 = yield Spawn(good())
            t2 = yield Spawn(bad())
            return (yield Gather(t1, t2))

        with pytest.raises(RuntimeError, match="gather fail"):
            doeff_run(scheduled(body()))

    def test_nested_task_error_propagates(self):
        """Error in a nested task propagates through Wait chain."""
        @do
        def inner():
            raise TypeError("deep error")
            yield

        @do
        def outer():
            t = yield Spawn(inner())
            return (yield Wait(t))

        @do
        def body():
            t = yield Spawn(outer())
            return (yield Wait(t))

        with pytest.raises(TypeError, match="deep error"):
            doeff_run(scheduled(body()))

    def test_try_except_catches_task_error(self):
        """User can catch task errors with try/except."""
        @do
        def failing():
            raise ValueError("caught me")
            yield

        @do
        def body():
            t = yield Spawn(failing())
            try:
                yield Wait(t)
                return "should not reach"
            except ValueError as e:
                return f"caught: {e}"

        assert doeff_run(scheduled(body())) == "caught: caught me"
