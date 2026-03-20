from __future__ import annotations

from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
SEGMENT_RS = CORE_ROOT / "src" / "segment.rs"
RUST_STORE_RS = CORE_ROOT / "src" / "rust_store.rs"
VM_DISPATCH_RS = CORE_ROOT / "src" / "vm" / "dispatch.rs"
VM_HANDLER_RS = CORE_ROOT / "src" / "vm" / "handler.rs"
SCHEDULER_RS = CORE_ROOT.parent / "doeff-core-effects" / "src" / "scheduler" / "mod.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    runtime_source, _, _ = source.rpartition("\n#[cfg(test)]")
    if runtime_source:
        return runtime_source
    return source


def test_segment_tracks_scope_parent_and_scoped_storage() -> None:
    source = _runtime_source(SEGMENT_RS)

    assert "pub scope_parent: Option<SegmentId>" in source, (
        "Segment must separate lexical scope lookup from dynamic caller flow."
    )
    assert "pub variables: HashMap<VarId, Value>" in source, (
        "Segment must own scoped variable storage for lexical bindings."
    )
    assert "pub scope_store" not in source, (
        "ScopeStore must be removed from Segment once lexical bindings live on segments."
    )


def test_rust_store_has_no_handler_specific_fields_left() -> None:
    source = _runtime_source(RUST_STORE_RS)

    assert "pub state:" not in source, "RustStore.state must be removed."
    assert "pub env:" not in source, "RustStore.env must be removed."
    assert "pub log:" not in source, "RustStore.log must be removed."


def test_spawn_reuses_live_scope_chain_without_scope_cloning() -> None:
    dispatch_source = _runtime_source(VM_DISPATCH_RS)

    assert "clone_spawn_scope_chain" not in dispatch_source, (
        "Shared-handler Spawn must not clone lexical ancestry into per-task handler copies."
    )
    assert "EvalReturnContinuation::ReturnToContinuation" in dispatch_source, (
        "Spawned continuations need a return anchor so task completion goes back to the scheduler."
    )
    assert "let mut return_anchor = Segment::new(Marker::fresh(), Some(current_seg_id));" in dispatch_source, (
        "Spawn must allocate a return anchor that re-enters the live caller chain."
    )
    assert "body_seg.scope_parent = scope_outside;" in dispatch_source, (
        "Spawn still needs lexical scope wiring for Local/Ask/Var inheritance."
    )


def test_handler_lookup_walks_dynamic_caller_chain_not_lexical_scope_chain() -> None:
    handler_source = _runtime_source(VM_HANDLER_RS)
    dispatch_source = _runtime_source(VM_DISPATCH_RS)

    assert "cursor = seg.caller;" in handler_source, (
        "Handler lookup helpers must follow dynamic caller links for shared-handler Spawn."
    )
    assert "let next = seg.caller;" in dispatch_source, (
        "Dispatch selection must scan the caller chain instead of lexical scope_parent ancestry."
    )


def test_scheduler_spawn_path_no_longer_requests_get_handlers() -> None:
    source = _runtime_source(SCHEDULER_RS)

    start = source.find("SchedulerPhase::SpawnAwaitTraceback")
    end = source.find("SchedulerPhase::SpawnAwaitContinuation")
    assert start != -1 and end != -1, "scheduler spawn phases must exist"
    spawn_block = source[start:end]

    assert "DoCtrl::GetHandlers" not in spawn_block, (
        "Spawn inheritance must come from lexical scope, not GetHandlers replay."
    )
