from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SEGMENT_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "segment.rs"
VAR_STORE_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "var_store.rs"
VM_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "vm.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    runtime_source, _, _ = source.rpartition("\n#[cfg(test)]")
    if runtime_source:
        return runtime_source
    return source


def test_fiber_runtime_source_does_not_store_handler_state_or_logs() -> None:
    source = _runtime_source(SEGMENT_RS)

    assert "pub state_store:" not in source, (
        "Fiber must not own handler state. SPEC-VM-019 Rev 5 requires state to live in VarStore."
    )
    assert "pub writer_log:" not in source, (
        "Fiber must not own writer logs. SPEC-VM-019 Rev 5 requires logs to live in VarStore."
    )
    assert "named_bindings" not in source, (
        "Fiber must not own named bindings. Lexical bindings belong in VarStore."
    )


def test_var_store_runtime_source_owns_handler_state_logs_and_bindings() -> None:
    source = _runtime_source(VAR_STORE_RS)

    assert "HashMap<SegmentId, HashMap<String, Value>>" in source, (
        "VarStore must own handler state entries after moving them off Fiber."
    )
    assert "HashMap<SegmentId, Vec<Value>>" in source, (
        "VarStore must own writer log entries after moving them off Fiber."
    )
    assert "HashMap<SegmentId, HashMap<HashedPyKey, Value>>" in source, (
        "VarStore must continue to own named bindings for lexical scope."
    )


def test_vm_runtime_source_does_not_keep_handler_state_side_tables() -> None:
    source = _runtime_source(VM_RS)

    for forbidden in (
        "scope_state_store:",
        "scope_writer_logs:",
        "retired_scope_state_store:",
        "retired_scope_writer_logs:",
    ):
        assert forbidden not in source, (
            "VM must not keep handler state/log side tables after they move into VarStore."
        )


def test_fiber_runtime_source_does_not_store_scope_id_or_epoch() -> None:
    source = _runtime_source(SEGMENT_RS)

    assert "pub scope_id:" not in source, (
        "Fiber must not carry scope_id. The VM should own the FiberId -> ScopeId mapping."
    )
    assert "pub persistent_epoch:" not in source, (
        "Fiber must not carry persistent_epoch once Arc snapshot reconciliation is gone."
    )


def test_vm_runtime_source_owns_scope_ids_without_epoch_reconciliation() -> None:
    source = _runtime_source(VM_RS)

    assert "HashMap<SegmentId, ScopeId>" in source, (
        "VM must own the FiberId -> ScopeId mapping after removing scope_id from Fiber."
    )
    assert "scope_persistent_epochs" not in source, (
        "VM must not keep scope_persistent_epochs after removing persistent_epoch from Fiber."
    )
    assert "retired_scope_persistent_epochs" not in source, (
        "VM must not keep retired epoch reconciliation tables after removing Arc snapshot state."
    )


def test_fiber_runtime_source_does_not_store_error_or_interceptor_runtime_state() -> None:
    source = _runtime_source(SEGMENT_RS)

    for forbidden in (
        "pub pending_error_context:",
        "pub throw_parent:",
        "pub interceptor_eval_depth:",
        "pub interceptor_skip_stack:",
    ):
        assert forbidden not in source, (
            "Fiber must not keep execution-local error/interceptor runtime state."
        )


def test_vm_runtime_source_owns_fiber_runtime_side_table() -> None:
    source = _runtime_source(VM_RS)

    assert "HashMap<SegmentId, FiberRuntimeState>" in source, (
        "VM must own per-fiber runtime state after removing error/interceptor fields from Fiber."
    )


def test_fiber_runtime_source_has_only_frames_parent_and_kind_fields() -> None:
    source = _runtime_source(SEGMENT_RS)
    fiber_match = re.search(r"pub struct Fiber \{(?P<body>.*?)\n\}", source, re.DOTALL)
    assert fiber_match is not None, "Fiber struct definition must exist in segment.rs."

    public_fields = re.findall(r"^\s*pub\s+([a-z_]+):", fiber_match.group("body"), re.MULTILINE)
    assert public_fields == ["frames", "parent", "kind"], (
        "SPEC-VM-019 Rev 5 requires Fiber to shrink to exactly frames + parent + handler/kind."
    )


def test_fiber_runtime_source_does_not_store_marker_field() -> None:
    source = _runtime_source(SEGMENT_RS)

    assert "pub marker:" not in source, (
        "Fiber must not store marker directly. SPEC-VM-019 Rev 5 folds marker into handler state."
    )
