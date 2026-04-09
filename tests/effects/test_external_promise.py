"""Tests for ExternalPromise - external world integration.

Tests the ExternalPromise mechanism for receiving results from
external code (threads, asyncio, processes, etc.).
"""

import threading
import time
from typing import Any

import pytest

from doeff import Gather, Spawn, async_run, default_handlers, do, run
from doeff import CreateExternalPromise, Wait


def _run_with_timeout(program_factory, timeout: float = 1.0) -> Any:
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            result["value"] = run(program_factory(), handlers=default_handlers())
        except BaseException as exc:  # pragma: no cover - test helper
            error["value"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    assert not thread.is_alive(), "program timed out waiting for external completion"

    if "value" in error:
        raise error["value"]

    return result["value"]


class TestExternalPromiseBasics:
    """Basic ExternalPromise functionality tests."""

    def test_create_external_promise(self) -> None:
        """CreateExternalPromise returns an ExternalPromise."""
        from doeff_core_effects.scheduler import ExternalPromise

        @do
        def program():
            promise = yield CreateExternalPromise()
            return promise

        result = run(program(), handlers=default_handlers())
        assert result.is_ok
        assert isinstance(result.value, ExternalPromise)
        assert result.value.id is not None

    def test_external_promise_has_future(self) -> None:
        """ExternalPromise.future returns a waitable Future."""
        from doeff_core_effects.scheduler import Future

        @do
        def program():
            promise = yield CreateExternalPromise()
            return promise.future

        result = run(program(), handlers=default_handlers())
        assert result.is_ok
        assert isinstance(result.value, Future)


class TestExternalPromiseCompletion:
    """Tests for completing ExternalPromise from external code."""

    def test_complete_from_thread(self) -> None:
        """ExternalPromise can be completed from another thread."""

        @do
        def program():
            promise = yield CreateExternalPromise()

            def worker():
                time.sleep(0.01)  # Simulate work
                promise.complete(42)

            thread = threading.Thread(target=worker)
            thread.start()

            result = yield Wait(promise.future)
            thread.join()
            return result

        result = run(program(), handlers=default_handlers())
        assert result.is_ok
        assert result.value == 42

    def test_fail_from_thread(self) -> None:
        """ExternalPromise can be failed from another thread."""

        @do
        def program():
            promise = yield CreateExternalPromise()

            def worker():
                time.sleep(0.01)
                promise.fail(ValueError("external error"))

            thread = threading.Thread(target=worker)
            thread.start()

            result = yield Wait(promise.future)
            thread.join()
            return result

        result = run(program(), handlers=default_handlers())
        assert result.is_err()
        assert isinstance(result.error, ValueError)
        assert "external error" in str(result.error)

    def test_complete_with_none(self) -> None:
        """ExternalPromise can be completed with None."""

        @do
        def program():
            promise = yield CreateExternalPromise()

            def worker():
                promise.complete(None)

            thread = threading.Thread(target=worker)
            thread.start()

            result = yield Wait(promise.future)
            thread.join()
            return result

        result = run(program(), handlers=default_handlers())
        assert result.is_ok
        assert result.value is None


class TestExternalPromiseWithAsyncRun:
    """Tests for ExternalPromise with async_run."""

    @pytest.mark.asyncio
    async def test_complete_from_thread_async(self) -> None:
        """ExternalPromise works with async_run."""

        @do
        def program():
            promise = yield CreateExternalPromise()

            def worker():
                time.sleep(0.01)
                promise.complete("async result")

            thread = threading.Thread(target=worker)
            thread.start()

            result = yield Wait(promise.future)
            thread.join()
            return result

        result = await async_run(program(), handlers=default_handlers())
        assert result.is_ok
        assert result.value == "async result"


class TestExternalPromiseMultiple:
    """Tests for multiple ExternalPromises."""

    def test_multiple_external_promises(self) -> None:
        """Multiple ExternalPromises can be waited on."""

        @do
        def program():
            promise1 = yield CreateExternalPromise()
            promise2 = yield CreateExternalPromise()

            def worker1():
                time.sleep(0.01)
                promise1.complete("first")

            def worker2():
                time.sleep(0.02)
                promise2.complete("second")

            thread1 = threading.Thread(target=worker1)
            thread2 = threading.Thread(target=worker2)
            thread1.start()
            thread2.start()

            result1 = yield Wait(promise1.future)
            result2 = yield Wait(promise2.future)
            thread1.join()
            thread2.join()
            return (result1, result2)

        result = run(program(), handlers=default_handlers())
        assert result.is_ok
        assert result.value == ("first", "second")

class TestExternalPromiseRunIsolation:
    """Regression coverage for scheduler state isolation across runs."""

    def test_external_promises_are_isolated_after_scheduler_run(self) -> None:
        @do
        def child(value: str):
            return value

        @do
        def scheduler_heavy_program():
            t1 = yield Spawn(child("left"))
            t2 = yield Spawn(child("right"))
            values = yield Gather(t1, t2)
            return tuple(values)

        scheduler_result = run(scheduler_heavy_program(), handlers=default_handlers())
        assert scheduler_result.is_ok
        assert scheduler_result.value == ("left", "right")

        @do
        def external_program(expected: str):
            promise = yield CreateExternalPromise()

            def worker():
                time.sleep(0.01)
                promise.complete(expected)

            thread = threading.Thread(target=worker)
            thread.start()
            value = yield Wait(promise.future)
            thread.join()
            return value

        first = _run_with_timeout(lambda: external_program("first"), timeout=1.0)
        assert first.is_ok
        assert first.value == "first"

        second = _run_with_timeout(lambda: external_program("second"), timeout=1.0)
        assert second.is_ok
        assert second.value == "second"
