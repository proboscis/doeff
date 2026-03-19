from __future__ import annotations

from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
SEGMENT_RS = CORE_ROOT / "src" / "segment.rs"
RUST_STORE_RS = CORE_ROOT / "src" / "rust_store.rs"
VM_DISPATCH_RS = CORE_ROOT / "src" / "vm" / "dispatch.rs"
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


def test_create_continuation_captures_scope_parent_for_spawn() -> None:
    source = _runtime_source(VM_DISPATCH_RS)

    assert "outside_scope" in source, (
        "CreateContinuation/ResumeContinuation must carry the spawn-site scope explicitly."
    )
    assert "scope_parent" in source, (
        "Continuation activation must preserve lexical scope separately from caller."
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
