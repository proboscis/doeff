from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doeff import Effect, Gather, Program, Spawn, do
from doeff._types_internal import EffectBase
from doeff.effects import ProgramCallStack
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
from doeff.traceback import build_doeff_traceback

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

    @do
    def observer(effect: Effect, k: object):
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
    @do
    def enrich(effect: Effect, k: object):
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

    @do
    def observer(effect: Effect, k: object):
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
    @do
    def exploding_handler(effect: Effect, _k: object):
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

    @do
    def exploding_handler(effect: Effect, _k: object):
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


def _active_chain_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [entry for entry in value if isinstance(entry, dict)]


def test_get_execution_context_returns_active_chain() -> None:
    @do
    def program() -> Program[object]:
        return (yield GetExecutionContext())

    result = run(program(), handlers=default_handlers())
    assert result.is_ok(), result.error
    context = result.value
    active_chain = getattr(context, "active_chain", None)
    entries = _active_chain_entries(active_chain)
    assert active_chain is not None
    assert any(entry.get("kind") == "program_yield" for entry in entries)


def test_get_execution_context_active_chain_has_args_repr() -> None:
    @do
    def with_args(x: int, *, y: int) -> Program[object]:
        return (yield GetExecutionContext())

    result = run(with_args(7, y=11), handlers=default_handlers())
    assert result.is_ok(), result.error
    entries = _active_chain_entries(getattr(result.value, "active_chain", None))
    program_entries = [entry for entry in entries if entry.get("kind") == "program_yield"]
    assert program_entries
    assert any(entry.get("args_repr") is not None for entry in program_entries)


def test_get_execution_context_active_chain_shows_active_dispatches() -> None:
    captured: dict[str, Any] = {}

    @dataclass(frozen=True, kw_only=True)
    class ProbeEffect(EffectBase):
        pass

    @do
    def inspector(effect: Effect, k: object):
        if isinstance(effect, ProbeEffect):
            captured["context"] = yield GetExecutionContext()
            return (yield Resume(k, "ok"))
        yield Pass()

    @do
    def program() -> Program[str]:
        return (yield ProbeEffect())

    result = run(program(), handlers=[*default_handlers(), inspector])
    assert result.is_ok(), result.error
    context = captured.get("context")
    assert context is not None
    entries = _active_chain_entries(getattr(context, "active_chain", None))
    effect_entries = [entry for entry in entries if entry.get("kind") == "effect_yield"]
    assert any(
        entry.get("result", {}).get("kind") == "active"
        for entry in effect_entries
        if isinstance(entry.get("result"), dict)
    )


def test_get_execution_context_active_chain_no_exception_site() -> None:
    @do
    def program() -> Program[object]:
        return (yield GetExecutionContext())

    result = run(program(), handlers=default_handlers())
    assert result.is_ok(), result.error
    entries = _active_chain_entries(getattr(result.value, "active_chain", None))
    assert not any(entry.get("kind") == "exception_site" for entry in entries)


def test_get_execution_context_on_error_still_works() -> None:
    @do
    def enrich(effect: Effect, k: object):
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


def test_get_execution_context_active_chain_renderable() -> None:
    @do
    def program() -> Program[object]:
        return (yield GetExecutionContext())

    result = run(program(), handlers=default_handlers())
    assert result.is_ok(), result.error
    context = result.value
    active_chain = getattr(context, "active_chain", ())
    tb = build_doeff_traceback(
        RuntimeError("active-chain snapshot"),
        trace_entries=[],
        active_chain_entries=active_chain,
        allow_active=True,
    )
    rendered = tb.format_default()
    assert "doeff Traceback (most recent call last):" in rendered


def test_program_call_stack_deprecation_warning() -> None:
    @do
    def body() -> Program[object]:
        stack = yield ProgramCallStack()
        return stack

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = run(body(), handlers=default_handlers())

    assert result.is_ok(), result.error
    warning_messages = [str(item.message) for item in caught]
    assert any(
        issubclass(item.category, DeprecationWarning)
        and "GetExecutionContext" in str(item.message)
        for item in caught
    )
    assert warning_messages


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

    @do
    def inspector(effect: Effect, k: object):
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
    scheduler_rs = ROOT / "packages" / "doeff-vm" / "src" / "scheduler.rs"
    if not scheduler_rs.exists():
        scheduler_rs = ROOT / "packages" / "doeff-core-effects" / "src" / "scheduler" / "mod.rs"
    scheduler_src = scheduler_rs.read_text()
    vm_rs = ROOT / "packages" / "doeff-vm" / "src" / "vm.rs"
    vm_sources = [vm_rs]
    if not vm_rs.exists():
        vm_rs = ROOT / "packages" / "doeff-vm-core" / "src" / "vm.rs"
        vm_sources = [
            vm_rs,
            vm_rs.parent / "vm" / "dispatch.rs",
            vm_rs.parent / "vm" / "step.rs",
            vm_rs.parent / "vm" / "vm_trace.rs",
        ]
    vm_src = "\n".join(path.read_text() for path in vm_sources if path.exists())
    assert "EXCEPTION_SPAWN_BOUNDARIES" not in scheduler_src
    assert "take_exception_spawn_boundaries" not in scheduler_src
    assert "take_exception_spawn_boundaries" not in vm_src
