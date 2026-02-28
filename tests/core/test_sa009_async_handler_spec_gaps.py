"""SA009 contract tests: async handler architecture and concurrency guarantees."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Any

import pytest
import doeff_vm

from doeff import (
    Await,
    Effect,
    EffectBase,
    Gather,
    Spawn,
    async_run,
    default_async_handlers,
    default_handlers,
    do,
    run,
)


ROOT = Path(__file__).resolve().parents[2]


def _result_is_ok(run_result: object) -> bool:
    is_ok = getattr(run_result, "is_ok", None)
    if callable(is_ok):
        return bool(is_ok())
    if isinstance(is_ok, bool):
        return is_ok
    raise AssertionError("RunResult does not expose is_ok as bool or method")


def _result_value(run_result: object) -> object:
    if hasattr(run_result, "value"):
        return getattr(run_result, "value")
    inner = getattr(run_result, "result", None)
    if inner is not None and hasattr(inner, "value"):
        return getattr(inner, "value")
    raise AssertionError("RunResult does not expose value")


def _assert_vm_handler_stack_matches_passed_handlers(
    *,
    passed_handlers: list[Any],
    vm_handler_stack: list[Any],
) -> None:
    """Assert VM handler stack preserves passed handlers.

    GetHandlers() returns handlers in stack order (top-most first), while
    handlers are passed in source order (left-to-right). Rust sentinels are
    represented as None identities in GetHandlers.
    """
    expected_stack = [handler if callable(handler) else None for handler in reversed(passed_handlers)]
    assert len(vm_handler_stack) == len(expected_stack)
    for expected, seen in zip(expected_stack, vm_handler_stack):
        if expected is None:
            assert seen is None, f"Expected Rust sentinel identity placeholder None, got {seen!r}"
        else:
            expected_func = getattr(expected, "func", None)
            expected_wrapped = getattr(expected, "__wrapped__", None)
            expected_original = getattr(expected, "original_func", None)
            expected_candidates = {expected, expected_func, expected_wrapped, expected_original}
            if seen in expected_candidates:
                continue

            seen_name = getattr(seen, "__qualname__", None) or getattr(seen, "__name__", None)
            candidate_names = {
                getattr(candidate, "__qualname__", None) or getattr(candidate, "__name__", None)
                for candidate in expected_candidates
                if candidate is not None
            }
            assert seen_name in candidate_names, (
                f"Handler mismatch: expected {expected!r} but VM saw {seen!r}. "
                "Handlers were modified between entrypoint and VM dispatch."
            )


class TestHandlerImmutabilityContract:
    @dataclass(frozen=True)
    class ProbeEffect(EffectBase):
        pass

    @staticmethod
    @do
    def _passthrough_handler(effect: Effect, k):
        yield doeff_vm.Delegate()

    @staticmethod
    @do
    def _probe_handler(effect: Effect, k):
        if isinstance(effect, TestHandlerImmutabilityContract.ProbeEffect):
            handler_stack = yield doeff_vm.GetHandlers()
            return (yield doeff_vm.Resume(k, handler_stack))
        yield doeff_vm.Delegate()

    @classmethod
    def _program(cls):
        @do
        def program():
            return (yield doeff_vm.Perform(cls.ProbeEffect()))

        return program()

    def test_run_handler_stack_matches_passed_handlers(self) -> None:
        handlers = [doeff_vm.await_handler, self._passthrough_handler, self._probe_handler]
        result = run(self._program(), handlers=handlers)
        assert _result_is_ok(result)
        _assert_vm_handler_stack_matches_passed_handlers(
            passed_handlers=handlers,
            vm_handler_stack=_result_value(result),
        )

    @pytest.mark.asyncio
    async def test_async_run_handler_stack_matches_passed_handlers(self) -> None:
        handlers = [doeff_vm.await_handler, self._passthrough_handler, self._probe_handler]
        result = await async_run(self._program(), handlers=handlers)
        assert _result_is_ok(result)
        _assert_vm_handler_stack_matches_passed_handlers(
            passed_handlers=handlers,
            vm_handler_stack=_result_value(result),
        )


class TestNoHandlerSwapContract:
    def test_no_normalize_async_handlers_function(self) -> None:
        import doeff.rust_vm as rust_vm

        assert not hasattr(rust_vm, "_normalize_async_handlers"), (
            "_normalize_async_handlers still exists in rust_vm.py. "
            "Handler swapping violates handler immutability invariant."
        )

    def test_no_needs_threaded_async_driver_function(self) -> None:
        import doeff.rust_vm as rust_vm

        assert not hasattr(rust_vm, "_needs_threaded_async_driver"), (
            "_needs_threaded_async_driver still exists in rust_vm.py. "
            "VM execution model must not depend on handler identity."
        )

    def test_no_run_async_call_in_thread_function(self) -> None:
        import doeff.rust_vm as rust_vm

        assert not hasattr(rust_vm, "_run_async_call_in_thread"), (
            "_run_async_call_in_thread still exists in rust_vm.py. "
            "async_run must not offload VM to background thread."
        )

    def test_rust_vm_source_has_no_handler_swap_patterns(self) -> None:
        source = (ROOT / "doeff" / "rust_vm.py").read_text(encoding="utf-8")

        assert "_normalize_async_handlers" not in source
        assert "_needs_threaded_async_driver" not in source
        assert "_run_async_call_in_thread" not in source
        assert "python_async_syntax_escape_handler" not in source, (
            "rust_vm.py must not reference python_async_syntax_escape_handler. "
            "Handler selection is the user's responsibility."
        )


class TestDefaultHandlerPresetsContract:
    def test_default_async_handlers_exists(self) -> None:
        from doeff import default_async_handlers as imported_default_async_handlers

        handlers = imported_default_async_handlers()
        assert isinstance(handlers, list)
        assert len(handlers) >= 5

    def test_default_handlers_differ_from_default_async_handlers(self) -> None:
        sync_handlers = default_handlers()
        async_handlers = default_async_handlers()
        assert sync_handlers != async_handlers, (
            "default_handlers() and default_async_handlers() must be different presets."
        )


class TestSA009AsyncConcurrencyContract:
    @pytest.mark.asyncio
    async def test_spawned_await_tasks_overlap_under_async_run(self) -> None:
        @do
        def child(label: str):
            value = yield Await(asyncio.sleep(0.12, result=label))
            return value

        @do
        def parent():
            t1 = yield Spawn(child("left"))
            t2 = yield Spawn(child("right"))
            values = yield Gather(t1, t2)
            return tuple(values)

        start = time.monotonic()
        result = await async_run(parent(), handlers=default_async_handlers())
        elapsed = time.monotonic() - start

        assert _result_is_ok(result)
        assert _result_value(result) == ("left", "right")
        assert elapsed < 0.18, (
            f"Spawned Await tasks did not overlap (elapsed={elapsed:.3f}s). "
            "Expected async handler path to allow concurrent progress."
        )


class TestAwaitHandlerEffectSystemContract:
    def test_rust_handler_source_has_no_blocking_await_runner(self) -> None:
        handler_rs = (ROOT / "packages" / "doeff-vm" / "src" / "handler.rs").read_text(
            encoding="utf-8"
        )
        assert "get_blocking_await_runner" not in handler_rs, (
            "handler.rs still contains get_blocking_await_runner. "
            "Await handlers must use the effect system (ExternalPromise + Wait), "
            "not bypass it with blocking executor calls."
        )

    def test_rust_handler_source_has_no_threadpoolexecutor(self) -> None:
        handler_rs = (ROOT / "packages" / "doeff-vm" / "src" / "handler.rs").read_text(
            encoding="utf-8"
        )
        assert "ThreadPoolExecutor" not in handler_rs, (
            "handler.rs still contains ThreadPoolExecutor. "
            "Await effect handling must go through the effect system."
        )


class TestAsyncRunThreadingContract:
    @pytest.mark.asyncio
    async def test_async_run_executes_on_caller_event_loop(self) -> None:
        caller_thread = threading.current_thread().ident
        observed_thread: int | None = None

        @do
        def program():
            nonlocal observed_thread
            observed_thread = threading.current_thread().ident

            async def _noop():
                return None

            _ = yield Await(_noop())
            return "done"

        result = await async_run(program(), handlers=default_async_handlers())

        assert _result_is_ok(result)
        assert _result_value(result) == "done"
        assert observed_thread is not None
        assert observed_thread == caller_thread, (
            "async_run stepped the program on a non-caller thread. "
            "VM stepping must remain on the caller event loop thread."
        )
