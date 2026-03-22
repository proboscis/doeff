from __future__ import annotations

from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
SEGMENT_RS = CORE_ROOT / "src" / "segment.rs"
VAR_STORE_RS = CORE_ROOT / "src" / "var_store.rs"
VM_DISPATCH_RS = CORE_ROOT / "src" / "vm" / "dispatch.rs"
VM_HANDLER_RS = CORE_ROOT / "src" / "vm" / "handler.rs"
SCHEDULER_RS = CORE_ROOT.parent / "doeff-core-effects" / "src" / "scheduler" / "mod.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    runtime_source, _, _ = source.rpartition("\n#[cfg(test)]")
    if runtime_source:
        return runtime_source
    return source


def test_fiber_uses_single_parent_chain_for_scope_and_handler_visibility() -> None:
    source = _runtime_source(SEGMENT_RS)

    assert "pub parent: Option<FiberId>" in source, (
        "Fiber must keep a single parent chain that serves both dynamic and lexical lookup."
    )
    assert "scope_parent:" not in source, (
        "Separate lexical scope chains must not exist; walk Fiber.parent instead."
    )
    assert "variables:" not in source, (
        "Lexical variable storage must not move onto Fiber fields."
    )
    assert "struct ScopeStore" not in source, (
        "ScopeStore should not be defined in the segment module."
    )


def test_var_store_replaces_deleted_rust_store_module() -> None:
    source = _runtime_source(VAR_STORE_RS)

    assert "global_state:" in source, "VarStore must own the single handler state heap."
    assert "root_scope_bindings:" in source, (
        "VarStore must keep root lexical bindings for Ask/Local resolution."
    )


def test_spawn_reuses_live_fiber_chain_without_scope_cloning() -> None:
    dispatch_source = _runtime_source(VM_DISPATCH_RS)

    assert "clone_spawn_scope_chain" not in dispatch_source, (
        "Spawn must not clone lexical ancestry into per-task copies."
    )
    assert "EvalReturnContinuation::ReturnToContinuation" in dispatch_source, (
        "Spawned continuations still need a return anchor into the live caller chain."
    )
    assert "scope_parent" not in dispatch_source, (
        "Spawn must not wire a separate lexical scope chain."
    )


def test_handler_lookup_walks_parent_chain() -> None:
    handler_source = _runtime_source(VM_HANDLER_RS)
    dispatch_source = _runtime_source(VM_DISPATCH_RS)

    assert "cursor = seg.parent;" in handler_source, (
        "Handler lookup must walk the live Fiber.parent chain."
    )
    assert "let next = seg.parent;" in dispatch_source, (
        "Dispatch selection must scan the parent chain instead of separate scope links."
    )


def test_scheduler_spawn_path_no_longer_requests_get_handlers() -> None:
    source = _runtime_source(SCHEDULER_RS)

    start = source.find("SchedulerPhase::SpawnAwaitTraceback")
    end = source.find("SchedulerPhase::SpawnAwaitContinuation")
    assert start != -1 and end != -1, "scheduler spawn phases must exist"
    spawn_block = source[start:end]

    assert "DoCtrl::GetHandlers" not in spawn_block, (
        "Spawn inheritance must come from the live fiber chain, not GetHandlers replay."
    )
