from __future__ import annotations

from pathlib import Path
import re

import pytest


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


# ---------------------------------------------------------------------------
# Phase 5: VM side-tables that must be eliminated
#
# OCaml 5 VM has ~5 fields: arena, current_fiber, heap, mode, pending.
# doeff VM still has dispatch side-tables that accumulate mutable state
# instead of deriving it from the fiber chain topology.
# These tests are xfail until Phase 5 completes.
# ---------------------------------------------------------------------------

DISPATCH_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "vm" / "dispatch.rs"
DISPATCH_OBSERVER_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "dispatch_observer.rs"


@pytest.mark.xfail(reason="Phase 5: DispatchObserver must be eliminated — derive from stack", strict=False)
def test_vm_source_does_not_have_dispatch_observer() -> None:
    """DispatchObserver is a side-table tracking dispatch state outside the stack.

    In OCaml 5, dispatch IS the topology change. There is no side-table.
    All dispatch context should be derivable from the fiber chain:
    - Walk chain, find handler fibers with active Program frames
    - Program frame effect_repr tells which effect triggered it
    - Error enrichment assembled on-demand from chain walk
    """
    source = _runtime_source(VM_RS)
    assert "dispatch_observer:" not in source, (
        "VM must not have a DispatchObserver. Dispatch context should be derived "
        "from the fiber chain topology, not accumulated in a side-table."
    )


@pytest.mark.xfail(reason="Phase 5: dispatch_observer.rs must not exist", strict=False)
def test_dispatch_observer_module_does_not_exist() -> None:
    """The dispatch_observer module should not exist.

    All dispatch tracking should be derivable from the stack structure.
    """
    assert not DISPATCH_OBSERVER_RS.exists(), (
        "dispatch_observer.rs must not exist. Dispatch state should be derived "
        "from the fiber chain, not tracked in a separate module."
    )


@pytest.mark.xfail(reason="Phase 5: continuation_registry must be eliminated", strict=False)
def test_vm_source_does_not_have_continuation_registry() -> None:
    """continuation_registry is a HashMap tracking continuations by ContId.

    In OCaml 5, continuations are owned values (moved fibers). They don't
    need a registry — ownership IS the tracking. A fiber is in the chain
    or in a continuation, tracked by the Continuation value itself.
    """
    source = _runtime_source(VM_RS)
    assert "continuation_registry:" not in source, (
        "VM must not have a continuation_registry. Continuation ownership "
        "should be tracked by the Continuation value itself, not a HashMap."
    )


@pytest.mark.xfail(reason="Phase 5: consumed_cont_ids must move to Continuation.consumed", strict=False)
def test_vm_source_does_not_have_consumed_cont_ids() -> None:
    """consumed_cont_ids is a HashSet tracking one-shot enforcement.

    In OCaml 5, one-shot is a flag on the continuation object itself
    (consumed: bool). Not a VM-level set.
    """
    source = _runtime_source(VM_RS)
    assert "consumed_cont_ids:" not in source, (
        "VM must not track consumed continuations in a HashSet. "
        "One-shot enforcement should be Continuation.consumed: bool."
    )


@pytest.mark.xfail(reason="Phase 5: installed_handlers/run_handlers should be on fiber chain", strict=False)
def test_vm_source_does_not_have_handler_lists() -> None:
    """installed_handlers and run_handlers are VM-level lists.

    In OCaml 5, handler visibility is determined by walking the fiber chain
    and finding handler delimiters. No separate handler list needed.
    """
    source = _runtime_source(VM_RS)
    assert "installed_handlers:" not in source, (
        "VM must not keep a separate installed_handlers list. "
        "Handler visibility comes from walking the fiber chain."
    )
    assert "run_handlers:" not in source, (
        "VM must not keep a separate run_handlers list. "
        "Handler visibility comes from walking the fiber chain."
    )
