"""Tests for ExternalPromise - external world integration.

Tests the ExternalPromise mechanism for receiving results from
external code (threads, asyncio, processes, etc.).
"""

import threading
import time

import pytest

from doeff import async_run, default_handlers, do, run
from doeff.effects import CreateExternalPromise, Wait

pytestmark = pytest.mark.skip(
    reason="Legacy CESK-era external promise semantics are not in the active rust_vm matrix."
)


class TestExternalPromiseBasics:
    """Basic ExternalPromise functionality tests."""

    def test_create_external_promise(self) -> None:
        """CreateExternalPromise returns an ExternalPromise."""
        from doeff.effects.external_promise import ExternalPromise

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
        from doeff.effects.spawn import Future

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

            threading.Thread(target=worker).start()

            result = yield Wait(promise.future)
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

            threading.Thread(target=worker).start()

            result = yield Wait(promise.future)
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

            threading.Thread(target=worker).start()

            result = yield Wait(promise.future)
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

            threading.Thread(target=worker).start()

            result = yield Wait(promise.future)
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

            threading.Thread(target=worker1).start()
            threading.Thread(target=worker2).start()

            result1 = yield Wait(promise1.future)
            result2 = yield Wait(promise2.future)
            return (result1, result2)

        result = run(program(), handlers=default_handlers())
        assert result.is_ok
        assert result.value == ("first", "second")
