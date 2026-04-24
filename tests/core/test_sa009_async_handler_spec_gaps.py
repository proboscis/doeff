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
RUST_SRC = ROOT / "packages" / "doeff-vm" / "src"
CORE_EFFECTS_SRC = ROOT / "packages" / "doeff-core-effects" / "src"


def _read_rust_source(filename: str) -> str:
    primary = RUST_SRC / filename
    if primary.exists():
        return primary.read_text(encoding="utf-8")
    fallback = {
        "effect.rs": CORE_EFFECTS_SRC / "effects" / "mod.rs",
        "handler.rs": CORE_EFFECTS_SRC / "handlers" / "mod.rs",
        "scheduler.rs": CORE_EFFECTS_SRC / "scheduler" / "mod.rs",
    }.get(filename)
    if fallback is not None and fallback.exists():
        return fallback.read_text(encoding="utf-8")
    return primary.read_text(encoding="utf-8")


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

class TestNoHandlerSwapContract:
    def test_no_normalize_async_handlers_function(self) -> None:
        import doeff_vm as rust_vm

        assert not hasattr(rust_vm, "_normalize_async_handlers"), (
            "_normalize_async_handlers still exists in rust_vm.py. "
            "Handler swapping violates handler immutability invariant."
        )

    def test_no_needs_threaded_async_driver_function(self) -> None:
        import doeff_vm as rust_vm

        assert not hasattr(rust_vm, "_needs_threaded_async_driver"), (
            "_needs_threaded_async_driver still exists in rust_vm.py. "
            "VM execution model must not depend on handler identity."
        )

    def test_no_run_async_call_in_thread_function(self) -> None:
        import doeff_vm as rust_vm

        assert not hasattr(rust_vm, "_run_async_call_in_thread"), (
            "_run_async_call_in_thread still exists in rust_vm.py. "
            "async_run must not offload VM to background thread."
        )


class TestDefaultHandlerPresetsContract:
    pass


class TestSA009AsyncConcurrencyContract:
    pass


class TestAwaitHandlerEffectSystemContract:
    pass


class TestAsyncRunThreadingContract:
    pass
