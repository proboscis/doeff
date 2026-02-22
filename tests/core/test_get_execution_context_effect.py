from __future__ import annotations

from pathlib import Path
from typing import Any

from doeff import Gather, Program, Spawn, do
from doeff.rust_vm import (
    Delegate,
    GetExecutionContext,
    GetTraceback,
    Pass,
    Resume,
    WithHandler,
    default_handlers,
    run,
)

ROOT = Path(__file__).resolve().parents[2]


def _entries_from_error(error: BaseException) -> list[Any]:
    context = getattr(error, "doeff_execution_context", None)
    if context is None:
        return []
    entries = getattr(context, "entries", None)
    if entries is None:
        return []
    return list(entries)


def test_generror_dispatches_get_execution_context_to_handlers() -> None:
    seen: list[str] = []

    def observer(effect: object, k: object):
        if isinstance(effect, GetExecutionContext):
            seen.append(type(effect).__name__)
            context = yield Delegate()
            return (yield Resume(k, context))
        yield Pass()

    @do
    def failing_program() -> Program[None]:
        raise ValueError("boom")

    wrapped = WithHandler(observer, failing_program())
    result = run(wrapped, handlers=default_handlers())
    assert result.is_err(), "program should fail"
    assert isinstance(result.error, ValueError)
    assert seen == ["GetExecutionContext"]


def test_handler_can_enrich_execution_context_before_throw() -> None:
    def enrich(effect: object, k: object):
        if isinstance(effect, GetExecutionContext):
            context = yield Delegate()
            context.add({"kind": "test_marker", "value": "enriched"})
            return (yield Resume(k, context))
        yield Pass()

    @do
    def failing_program() -> Program[None]:
        raise ValueError("boom")

    result = run(failing_program(), handlers=[*default_handlers(), enrich])
    assert result.is_err()
    assert isinstance(result.error, ValueError)
    entries = _entries_from_error(result.error)
    assert any(isinstance(entry, dict) and entry.get("kind") == "test_marker" for entry in entries)


def test_all_handlers_pass_falls_back_to_original_exception() -> None:
    @do
    def failing_program() -> Program[None]:
        raise RuntimeError("boom")

    result = run(failing_program(), handlers=[])
    assert result.is_err()
    assert isinstance(result.error, RuntimeError)
    assert getattr(result.error, "doeff_execution_context", None) is None


def test_base_exception_bypasses_get_execution_context_conversion() -> None:
    seen: list[str] = []

    def observer(effect: object, k: object):
        if isinstance(effect, GetExecutionContext):
            seen.append("called")
            context = yield Delegate()
            return (yield Resume(k, context))
        yield Pass()

    @do
    def failing_program() -> Program[None]:
        raise KeyboardInterrupt("stop")

    wrapped = WithHandler(observer, failing_program())
    result = run(wrapped, handlers=default_handlers())
    assert result.is_err()
    assert isinstance(result.error, KeyboardInterrupt)
    assert seen == []


def test_handler_throw_during_enrichment_chains_original_as_cause() -> None:
    def exploding_handler(effect: object, _k: object):
        if isinstance(effect, GetExecutionContext):
            raise RuntimeError("enrichment failed")
        yield Pass()

    @do
    def failing_program() -> Program[None]:
        raise ValueError("boom")

    result = run(failing_program(), handlers=[*default_handlers(), exploding_handler])
    assert result.is_err()
    assert isinstance(result.error, RuntimeError)
    assert isinstance(result.error.__cause__, ValueError)


def test_nested_generror_guard_blocks_recursive_error_dispatch() -> None:
    calls: list[int] = []

    def exploding_handler(effect: object, _k: object):
        if isinstance(effect, GetExecutionContext):
            calls.append(1)
            raise RuntimeError("enrichment failed")
        yield Pass()

    @do
    def failing_program() -> Program[None]:
        raise ValueError("boom")

    result = run(failing_program(), handlers=[*default_handlers(), exploding_handler])
    assert result.is_err()
    assert isinstance(result.error, RuntimeError)
    assert len(calls) == 1, "GetExecutionContext should not recurse on enrichment failure"


def test_user_can_yield_get_execution_context_directly() -> None:
    @do
    def program() -> Program[object]:
        context = yield GetExecutionContext()
        return context

    result = run(program(), handlers=default_handlers())
    assert result.is_ok(), result.error
    context = result.value
    assert type(context).__name__ == "ExecutionContext"
    assert isinstance(context.entries, list)


def test_cross_task_propagation_accumulates_spawn_boundaries() -> None:
    @do
    def leaf() -> Program[None]:
        raise ValueError("leaf boom")

    @do
    def child() -> Program[None]:
        task = yield Spawn(leaf())
        _ = yield Gather(task)
        return None

    @do
    def parent() -> Program[None]:
        task = yield Spawn(child())
        _ = yield Gather(task)
        return None

    result = run(parent(), handlers=default_handlers())
    assert result.is_err()
    assert isinstance(result.error, ValueError)
    entries = _entries_from_error(result.error)
    spawn_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("kind") == "spawn_boundary"
    ]
    assert len(spawn_entries) >= 1
    assert all("task_id" in entry for entry in spawn_entries)


def test_get_traceback_is_available_during_get_execution_context_dispatch() -> None:
    captured: dict[str, list[object]] = {}

    def inspector(effect: object, k: object):
        if isinstance(effect, GetExecutionContext):
            hops = yield GetTraceback(k)
            captured["hops"] = hops
            context = yield Delegate()
            return (yield Resume(k, context))
        yield Pass()

    @do
    def failing_program() -> Program[None]:
        raise ValueError("boom")

    result = run(failing_program(), handlers=[*default_handlers(), inspector])
    assert result.is_err()
    assert isinstance(result.error, ValueError)
    hops = captured.get("hops")
    assert isinstance(hops, list)
    assert hops


def test_exception_spawn_boundaries_global_removed() -> None:
    scheduler_src = (ROOT / "packages" / "doeff-vm" / "src" / "scheduler.rs").read_text()
    vm_src = (ROOT / "packages" / "doeff-vm" / "src" / "vm.rs").read_text()
    assert "EXCEPTION_SPAWN_BOUNDARIES" not in scheduler_src
    assert "take_exception_spawn_boundaries" not in scheduler_src
    assert "take_exception_spawn_boundaries" not in vm_src
