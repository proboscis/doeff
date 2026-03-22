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
DISPATCH_STATE_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "dispatch_state.rs"


# NOTE ON TEST DESIGN: These tests check for architectural patterns, not just
# field names. Renaming a field does NOT satisfy the test — the underlying
# data structure (HashMap, HashSet, Vec) tracking the same concept must be gone.


@pytest.mark.xfail(reason="Phase 5: dispatch side-table module must be eliminated", strict=False)
def test_no_dispatch_side_table_module_exists() -> None:
    """No separate module should track dispatch state outside the fiber chain.

    In OCaml 5, dispatch IS the topology change. All dispatch context is
    derivable from the fiber chain (walk chain, find handler fibers with
    active Program frames, read effect_repr). No side-table module needed.

    This test catches both dispatch_observer.rs and dispatch_state.rs (renamed).
    """
    assert not DISPATCH_OBSERVER_RS.exists(), (
        "dispatch_observer.rs must not exist."
    )
    assert not DISPATCH_STATE_RS.exists(), (
        "dispatch_state.rs must not exist (renamed dispatch_observer is still a side-table)."
    )


@pytest.mark.xfail(reason="Phase 5: VM must not have dispatch tracking HashMap", strict=False)
def test_vm_source_does_not_have_dispatch_tracking_map() -> None:
    """VM must not have any HashMap tracking dispatch state.

    This catches: dispatch_observer, dispatch_state, DispatchObserver,
    DispatchState, DispatchContext, or any HashMap<DispatchId, ...> on the VM.
    Renaming the field does not satisfy this test.
    """
    source = _runtime_source(VM_RS)
    # Check for any dispatch-tracking struct as a VM field
    for pattern in [
        "DispatchObserver",
        "DispatchState",
        "dispatch_observer:",
        "dispatch_state:",
        "HashMap<DispatchId",
    ]:
        assert pattern not in source, (
            f"VM must not have dispatch tracking ({pattern}). "
            "Dispatch context should be derived from the fiber chain topology."
        )


@pytest.mark.xfail(reason="Phase 5: no ContId→Continuation HashMap on VM", strict=False)
def test_vm_source_does_not_have_continuation_map() -> None:
    """VM must not have any HashMap mapping ContId to Continuation.

    In OCaml 5, continuations are owned values passed through the call chain.
    No registry, no store, no lookup table. Ownership IS tracking.

    This catches: continuation_registry, continuations, ContinuationStore,
    or any HashMap<ContId, Continuation> under any name.
    """
    source = _runtime_source(VM_RS)
    for pattern in [
        "HashMap<ContId, Continuation>",
        "continuation_registry:",
        "ContinuationStore",
        "continuations:",
    ]:
        assert pattern not in source, (
            f"VM must not have a continuation map ({pattern}). "
            "Continuations are owned values, not registered in a HashMap."
        )


@pytest.mark.xfail(reason="Phase 5: no HashSet tracking consumed continuations on VM", strict=False)
def test_vm_source_does_not_have_consumed_tracking_set() -> None:
    """VM must not have any HashSet tracking consumed continuation IDs.

    One-shot enforcement must be ONLY on the Continuation object itself
    (consumed: bool). No VM-level set under any name.

    This catches: consumed_cont_ids, consumed_continuations, or any
    HashSet<ContId> on the VM.
    """
    source = _runtime_source(VM_RS)
    for pattern in [
        "HashSet<ContId>",
        "consumed_cont_ids:",
        "consumed_continuations:",
    ]:
        assert pattern not in source, (
            f"VM must not track consumed continuations ({pattern}). "
            "One-shot is Continuation.consumed: bool, not a VM-level set."
        )


@pytest.mark.xfail(reason="Phase 5: no handler Vec lists on VM", strict=False)
def test_vm_source_does_not_have_handler_storage() -> None:
    """VM must not store handlers in Vec lists.

    In OCaml 5, handler visibility comes from walking the fiber chain
    and finding handler delimiters. No separate storage needed.

    This catches: installed_handlers, run_handlers, HandlerStore,
    handlers:, or any Vec<InstalledHandler>/Vec<KleisliRef> on VM.
    """
    source = _runtime_source(VM_RS)
    for pattern in [
        "installed_handlers:",
        "run_handlers:",
        "HandlerStore",
        "Vec<InstalledHandler>",
        "Vec<KleisliRef>",
    ]:
        assert pattern not in source, (
            f"VM must not store handlers in lists ({pattern}). "
            "Handler visibility comes from walking the fiber chain."
        )
