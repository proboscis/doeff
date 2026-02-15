"""TDD red tests for async handler spec gaps.

These tests intentionally codify strict spec/document contracts that are
currently missing or violated. They should fail until the gaps are fixed.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import re
import time

import pytest

from doeff import Await, Gather, Spawn, async_run, default_handlers, do


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


class TestSA009AwaitHandlerExistenceContract:
    """Spec/document contract: await handlers must exist and be importable."""

    def test_future_module_exposes_sync_and_async_await_handlers(self) -> None:
        future_mod = importlib.import_module("doeff.effects.future")
        sync_name = "_".join(("sync", "await", "handler"))
        async_name = "_".join(("python", "async", "syntax", "escape", "handler"))

        assert hasattr(
            future_mod,
            sync_name,
        ), "missing doeff.effects.future.sync_await_handler"
        assert hasattr(
            future_mod,
            async_name,
        ), "missing doeff.effects.future.python_async_syntax_escape_handler"

        assert callable(getattr(future_mod, sync_name))
        assert callable(getattr(future_mod, async_name))

    def test_future_module_all_exports_include_required_handlers(self) -> None:
        import doeff.effects.future as future_mod

        exports = set(getattr(future_mod, "__all__", []))
        assert "sync_await_handler" in exports
        assert "python_async_syntax_escape_handler" in exports

    def test_future_module_contains_handler_definitions_in_source(self) -> None:
        source = (ROOT / "doeff" / "effects" / "future.py").read_text(encoding="utf-8")

        assert re.search(r"def\s+sync_await_handler\s*\(", source)
        assert re.search(r"def\s+python_async_syntax_escape_handler\s*\(", source)
        assert "PythonAsyncSyntaxEscape" in source


class TestSA009ExternalWaitHandlerExistenceContract:
    """Spec/document contract: scheduler external wait handlers must exist."""

    def test_scheduler_internal_module_exposes_external_wait_handlers(self) -> None:
        scheduler_internal = importlib.import_module("doeff.effects.scheduler_internal")
        sync_name = "_".join(("sync", "external", "wait", "handler"))
        async_name = "_".join(("async", "external", "wait", "handler"))

        assert hasattr(
            scheduler_internal,
            sync_name,
        ), "missing doeff.effects.scheduler_internal.sync_external_wait_handler"
        assert hasattr(
            scheduler_internal,
            async_name,
        ), "missing doeff.effects.scheduler_internal.async_external_wait_handler"

        assert callable(getattr(scheduler_internal, sync_name))
        assert callable(getattr(scheduler_internal, async_name))

    def test_scheduler_internal_all_exports_include_external_wait_handlers(self) -> None:
        import doeff.effects.scheduler_internal as scheduler_internal

        exports = set(getattr(scheduler_internal, "__all__", []))
        assert "sync_external_wait_handler" in exports
        assert "async_external_wait_handler" in exports
        assert "WaitForExternalCompletion" in exports

    def test_scheduler_internal_contains_external_wait_handler_definitions(self) -> None:
        source = (ROOT / "doeff" / "effects" / "scheduler_internal.py").read_text(encoding="utf-8")

        assert re.search(r"def\s+sync_external_wait_handler\s*\(", source)
        assert re.search(r"def\s+async_external_wait_handler\s*\(", source)
        assert "WaitForExternalCompletion" in source
        assert "run_in_executor" in source
        assert "PythonAsyncSyntaxEscape" in source


class TestSA009AsyncConcurrencyContract:
    """Behavior contract: async path must be event-loop friendly and concurrent."""

    @pytest.mark.asyncio
    async def test_async_run_await_does_not_block_event_loop(self) -> None:
        tick_count = 0
        stop = asyncio.Event()

        async def ticker() -> None:
            nonlocal tick_count
            while not stop.is_set():
                tick_count += 1
                await asyncio.sleep(0.005)

        @do
        def program():
            value = yield Await(asyncio.sleep(0.12, result="ok"))
            return value

        ticker_task = asyncio.create_task(ticker())
        try:
            result = await async_run(program(), handlers=default_handlers())
        finally:
            stop.set()
            await ticker_task

        assert _result_is_ok(result)
        assert _result_value(result) == "ok"
        assert tick_count >= 8, (
            "Event loop made too little progress while Await was in-flight; "
            "async path appears blocking"
        )

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
        result = await async_run(parent(), handlers=default_handlers())
        elapsed = time.monotonic() - start

        assert _result_is_ok(result)
        assert _result_value(result) == ("left", "right")
        assert elapsed < 0.18, (
            f"Spawned Await tasks did not overlap (elapsed={elapsed:.3f}s). "
            "Expected async handler path to allow concurrent progress."
        )
