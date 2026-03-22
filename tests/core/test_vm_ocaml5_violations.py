"""Exhaustive OCaml 5 architecture violation detection tests.

Each test detects a specific violation identified in the 2026-03-22 audit.
Tests are xfail — they FAIL when the violation EXISTS, PASS when it's FIXED.
This file drives TDD: fix violations until all xfail markers can be removed.

Target architecture (OCaml 5):
  Fiber: frames + handler(Option) + parent. Nothing else.
  VM: arena + current_fiber + var_store + mode + pending. Nothing else.
  Continuation: fibers(Vec<FiberId>) + consumed(bool). Nothing else.
  Dispatch: pure topology change. No side-tables.
  TraceState: derive from fiber chain walk. No accumulated maps.
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
SEGMENT_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "segment.rs"
VM_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "vm.rs"
CONTINUATION_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "continuation.rs"
TRACE_STATE_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "trace_state.rs"
DISPATCH_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "vm" / "dispatch.rs"
FRAME_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "frame.rs"
VAR_STORE_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "var_store.rs"
DISPATCH_STATE_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "dispatch_state.rs"
DISPATCH_OBSERVER_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "dispatch_observer.rs"


def _src(path: Path) -> str:
    """Read runtime source (exclude #[cfg(test)] blocks)."""
    source = path.read_text(encoding="utf-8")
    runtime, _, _ = source.rpartition("\n#[cfg(test)]")
    return runtime if runtime else source


def _struct_body(source: str, name: str) -> str | None:
    """Extract the body of a pub struct definition."""
    m = re.search(rf"pub struct {name}\s*\{{(?P<body>.*?)\n\}}", source, re.DOTALL)
    return m.group("body") if m else None


def _pub_fields(struct_body: str) -> list[str]:
    """Extract public field names from a struct body."""
    return re.findall(r"^\s*pub\s+([a-z_]+):", struct_body, re.MULTILINE)


def _all_fields(struct_body: str) -> list[str]:
    """Extract all field names (pub and private) from a struct body."""
    return re.findall(r"^\s*(?:pub(?:\(crate\))?\s+)?([a-z_]+):", struct_body, re.MULTILINE)


# ============================================================================
# FIBER VIOLATIONS (segment.rs)
# Target: Fiber { frames, parent, kind } — 3 fields, kind = Option<Handler>
# ============================================================================


def test_v01_fiberkind_should_be_option_handler():
    """FiberKind should be Option<HandlerDelimiter>, not a 4-variant enum."""
    src = _src(SEGMENT_RS)
    assert "InterceptorBoundary" not in src, "FiberKind must not have InterceptorBoundary variant"
    assert "MaskBoundary" not in src, "FiberKind must not have MaskBoundary variant"


def test_v02_normal_fiber_no_marker():
    """Normal (non-handler) fibers should not carry a Marker."""
    src = _src(SEGMENT_RS)
    normal_match = re.search(r"Normal\s*\{(?P<body>[^}]*)\}", src)
    assert normal_match is None or "marker" not in normal_match.group("body"), \
        "Normal fibers must not carry a marker field"


def test_v05_no_segment_type_aliases():
    """Old Segment/SegmentKind names should not be exported."""
    src = _src(SEGMENT_RS)
    assert "type Segment = Fiber" not in src, "Remove Segment type alias"
    assert "type SegmentKind = FiberKind" not in src, "Remove SegmentKind type alias"


def test_v06_no_scope_store_in_segment():
    """ScopeStore should not be defined in the segment/fiber module."""
    src = _src(SEGMENT_RS)
    assert "struct ScopeStore" not in src, "ScopeStore does not belong in segment module"


def test_v07_fiber_not_clone():
    """Fiber must not implement Clone. Fibers are moved, never copied."""
    src = _src(SEGMENT_RS)
    assert "impl Clone for Fiber" not in src, "Fiber must not impl Clone"
    assert "impl Clone for Segment" not in src, "Segment (Fiber alias) must not impl Clone"


# ============================================================================
# VM VIOLATIONS (vm.rs)
# Target: VM { arena, current_fiber, var_store, mode, pending_python }
# ============================================================================


def test_v08_no_handler_store_on_vm():
    """VM must not have a handler list. Derive from fiber chain."""
    src = _src(VM_RS)
    for pattern in ["HandlerStore", "Vec<InstalledHandler>", "Vec<KleisliRef>",
                    "installed_handlers:", "run_handlers:", "handlers:"]:
        assert pattern not in src, f"VM must not have handler storage ({pattern})"


def test_v09_no_rust_store_on_vm():
    """VM must not have a separate RustStore. One heap (VarStore) only."""
    src = _src(VM_RS)
    assert "rust_store:" not in src, "VM must not have rust_store — use VarStore only"


def test_v10_no_env_store_on_vm():
    """VM must not have a separate env_store. One heap (VarStore) only."""
    src = _src(VM_RS)
    assert "env_store:" not in src, "VM must not have env_store — use VarStore only"


@pytest.mark.xfail(reason="V13: TraceState accumulated dispatch maps", strict=False)
def test_v13_no_trace_state_dispatch_maps():
    """TraceState must not accumulate dispatch display maps. Derive from stack."""
    src = _src(TRACE_STATE_RS)
    assert "dispatch_displays:" not in src, \
        "TraceState must not have dispatch_displays HashMap — derive from fiber chain walk"
    assert "HashMap<DispatchId, DispatchDisplayState>" not in src, \
        "No dispatch display accumulation"


def test_v14_no_dispatch_state_on_vm():
    """VM must not have dispatch_state/DispatchState. Dispatch = topology change."""
    src = _src(VM_RS)
    for pattern in ["dispatch_state:", "DispatchState", "dispatch_observer:"]:
        assert pattern not in src, f"VM must not have dispatch side-table ({pattern})"


def test_v15_no_fiber_runtime_sidetable():
    """VM must not have per-fiber runtime side-table. Fields belong on fiber or as registers."""
    src = _src(VM_RS)
    assert "fiber_runtime:" not in src, \
        "VM must not have fiber_runtime HashMap — this is relocated fiber fields as a side-table"
    assert "HashMap<SegmentId, FiberRuntimeState>" not in src


def test_v16_no_scope_ids_on_vm():
    """VM must not have scope_ids. Variables are VarId-addressed ref cells."""
    src = _src(VM_RS)
    assert "scope_ids:" not in src, "VM must not have scope_ids HashMap"


def test_v17_no_scope_parents():
    """VM must not have scope_parents. One parent chain only (fiber.parent)."""
    src = _src(VM_RS)
    assert "scope_parents:" not in src, \
        "VM must not have scope_parents — OCaml 5 has one parent chain"


def test_v18_no_parent_redirects():
    """VM must not have segment_parent_redirects. With move semantics, no stale pointers."""
    src = _src(VM_RS)
    assert "segment_parent_redirects:" not in src, \
        "VM must not have redirect map — move semantics means no stale pointers"


def test_v19_no_completed_snapshots():
    """VM must not cache completed state/log snapshots."""
    src = _src(VM_RS)
    assert "completed_state_entries_snapshot:" not in src
    assert "completed_log_entries_snapshot:" not in src


def test_v21_no_throw_parent():
    """No per-fiber throw_parent. Exceptions propagate by unwinding the chain."""
    src = _src(VM_RS)
    assert "throw_parent:" not in src, \
        "throw_parent stores a continuation per fiber — OCaml 5 unwinds the chain instead"


# ============================================================================
# CONTINUATION VIOLATIONS (continuation.rs)
# Target: Continuation { fibers: Vec<FiberId>, consumed: bool }
# ============================================================================


def test_v24_no_owns_fibers():
    """Continuation must not have owns_fibers. With move semantics, one owner always."""
    src = _src(CONTINUATION_RS)
    assert "owns_fibers:" not in src, \
        "owns_fibers implies multiple copies can exist — violates move semantics"


def test_v25_no_arc_consumed():
    """One-shot is consumed:bool on the single owner. No shared Arc needed."""
    src = _src(CONTINUATION_RS)
    assert "Arc<AtomicBool>" not in src, \
        "Arc<AtomicBool> consumed_state only needed when multiple copies exist"


def test_v26_no_arc_mutex_metadata():
    """No shared mutable metadata. Continuation is owned, metadata is plain fields."""
    src = _src(CONTINUATION_RS)
    assert "Arc<Mutex<ContinuationMetadata>>" not in src, \
        "Shared mutable metadata only needed when multiple copies exist"


def test_v27_no_unstarted_on_continuation():
    """Continuation = detached fibers. Unstarted programs should be a separate type."""
    src = _src(CONTINUATION_RS)
    body = _struct_body(src, "Continuation")
    assert body is not None
    assert "unstarted:" not in body, \
        "Continuation should not hold unstarted programs — separate type"


def test_v28_continuation_not_clone():
    """Continuation must not implement Clone. Move-only type."""
    src = _src(CONTINUATION_RS)
    assert "impl Clone for Continuation" not in src, \
        "Continuation must not impl Clone — move semantics means one owner"


def test_v29_no_clone_for_dispatch():
    """No forking continuations. One set of fibers, one owner."""
    src = _src(CONTINUATION_RS)
    assert "fn clone_for_dispatch" not in src, \
        "clone_for_dispatch creates two owners of the same fibers — violates move semantics"


def test_v23_no_dispatch_id_on_continuation():
    """Continuation should not link to dispatch side-tables."""
    src = _src(CONTINUATION_RS)
    body = _struct_body(src, "Continuation")
    assert body is not None
    assert "dispatch_id:" not in body, \
        "Continuation must not carry dispatch_id — dispatch is topology, not identity"


# ============================================================================
# TRACE STATE VIOLATIONS (trace_state.rs)
# Target: derive everything from fiber chain walk
# ============================================================================


@pytest.mark.xfail(reason="V30: dispatch_displays accumulated HashMap", strict=False)
def test_v30_no_dispatch_displays():
    """TraceState must not accumulate dispatch display state."""
    src = _src(TRACE_STATE_RS)
    assert "dispatch_displays:" not in src
    assert "DispatchDisplayState" not in src


@pytest.mark.xfail(reason="V31: frame_stack shadow copy", strict=False)
def test_v31_no_shadow_frame_stack():
    """TraceState must not maintain a shadow frame stack. The fiber chain IS the stack."""
    src = _src(TRACE_STATE_RS)
    assert "frame_stack:" not in src, \
        "TraceState must not have frame_stack — walk the fiber chain instead"


# ============================================================================
# DISPATCH STATE VIOLATIONS
# Target: no dispatch_state.rs, no dispatch_observer.rs
# ============================================================================


def test_v32_no_dispatch_state_module():
    """No dispatch state module should exist."""
    assert not DISPATCH_STATE_RS.exists(), "dispatch_state.rs must not exist"
    assert not DISPATCH_OBSERVER_RS.exists(), "dispatch_observer.rs must not exist"


# ============================================================================
# FRAME VIOLATIONS (frame.rs)
# Target: no interceptor-specific frames, no Clone, no chain snapshots
# ============================================================================


def test_v34_no_interceptor_frames():
    """No interceptor-specific frame variants."""
    src = _src(FRAME_RS)
    assert "InterceptorApply" not in src, "Frame must not have InterceptorApply variant"
    assert "InterceptorEval" not in src, "Frame must not have InterceptorEval variant"


def test_v35_no_chain_snapshot_on_frame():
    """Frames must not hold snapshots of handler/interceptor chains."""
    src = _src(FRAME_RS)
    assert "Arc<Vec<InterceptorChainLink>>" not in src, \
        "Frames must not snapshot chain state — derive from topology"


@pytest.mark.xfail(reason="V36: InterceptBodyReturn frame variant", strict=False)
def test_v36_no_intercept_body_return_frame():
    """No interceptor-specific return frame."""
    src = _src(FRAME_RS)
    assert "InterceptBodyReturn" not in src


@pytest.mark.xfail(reason="V38: Frame implements Clone", strict=False)
def test_v38_frame_not_clone():
    """Frame must not implement Clone. Frames live on fibers which are moved."""
    src = _src(FRAME_RS)
    assert "impl Clone for Frame" not in src, "Frame must not impl Clone"


# ============================================================================
# VARSTORE VIOLATIONS (var_store.rs)
# Target: flat HashMap<VarId, Value> — no segment-keyed maps
# ============================================================================


def test_v39_no_segment_keyed_state():
    """VarStore state should be VarId-addressed, not segment-keyed."""
    src = _src(VAR_STORE_RS)
    assert "state_by_segment:" not in src, \
        "VarStore must use VarId-addressed ref cells, not segment-keyed maps"


def test_v40_no_segment_keyed_logs():
    """Writer logs should be VarId-addressed ref cells."""
    src = _src(VAR_STORE_RS)
    assert "writer_logs_by_segment:" not in src


def test_v41_no_segment_keyed_bindings():
    """Bindings should be VarId-addressed ref cells."""
    src = _src(VAR_STORE_RS)
    assert "bindings_by_segment:" not in src


def test_v42_no_segment_keyed_overrides():
    """No per-segment override layers. One heap, one value per VarId."""
    src = _src(VAR_STORE_RS)
    assert "overrides_by_segment:" not in src
