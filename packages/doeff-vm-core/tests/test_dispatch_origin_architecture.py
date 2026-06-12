"""Guard-layer tests: dispatch-origin elimination (SPEC-VM-020 Phase 1b).

These tests assert that the old dispatch-origin / dispatch-state side-table
infrastructure has been fully removed.  Frame, Segment, and VM runtime code
must not contain remnants of the deleted DispatchOrigin / HandlerDispatch /
DispatchState machinery.

Tests that referenced removed files (dispatch.rs, dispatch_state.rs,
vm_trace.rs) or removed functions (current_interceptor_chain,
start_dispatch, handle_dispatch_resume, current_handler_identity_for_dispatch)
were deleted — guarding non-existent code is vacuously true.
"""

from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[1]
FRAME_RS = CORE_ROOT / "src/frame.rs"
SEGMENT_RS = CORE_ROOT / "src/segment.rs"
VM_RS = CORE_ROOT / "src/vm.rs"
VM_DISPATCH_RS = CORE_ROOT / "src/vm/dispatch.rs"
VM_STEP_RS = CORE_ROOT / "src/vm/step.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    return source.split("#[cfg(test)]", 1)[0]


def _vm_runtime_source() -> str:
    return "\n".join(
        _runtime_source(path)
        for path in (VM_RS, VM_DISPATCH_RS, VM_STEP_RS)
        if path.exists()
    )


def test_frame_runtime_has_no_dispatch_special_frames() -> None:
    source = _runtime_source(FRAME_RS)
    assert "HandlerDispatch {" not in source, (
        "Dispatch ownership must no longer live in Frame::HandlerDispatch."
    )
    assert "DispatchOrigin {" not in source, (
        "Dispatch ownership must no longer live in Frame::DispatchOrigin."
    )


def test_segment_has_no_dispatch_state_fields() -> None:
    source = _runtime_source(SEGMENT_RS)

    assert "pub struct HandlerDispatchState" not in source, (
        "Pure stack-machine dispatch must not keep HandlerDispatchState on Segment."
    )
    assert "pub struct DispatchOriginState" not in source, (
        "Pure stack-machine dispatch must not keep DispatchOriginState on Segment."
    )
    assert "pub handler_dispatch: Option<HandlerDispatchState>" not in source, (
        "Segments must not own active handler-dispatch state."
    )
    assert "pub dispatch_origin: Option<DispatchOriginState>" not in source, (
        "Segments must not own dispatch-origin state."
    )
    assert "pub dispatch_id: Option<DispatchId>" not in source, (
        "Dispatch correlation must move to an observer, not Segment."
    )


def test_handler_segment_setup_has_no_dispatch_state_writes() -> None:
    source = _vm_runtime_source()

    assert "handler_seg.handler_dispatch = Some(" not in source, (
        "Handler segment setup must not install handler dispatch state directly on Segment."
    )
    assert "handler_seg.dispatch_origin = Some(" not in source, (
        "Handler segment setup must not install dispatch origin state directly on Segment."
    )
    assert "handler_seg.dispatch_id = Some(" not in source, (
        "Handler segment setup must not stamp dispatch ids onto Segment."
    )
    assert "handler_seg.push_frame(Frame::HandlerDispatch" not in source
    assert "handler_seg.push_frame(Frame::DispatchOrigin" not in source
    assert "prompt_seg.push_frame(Frame::DispatchOrigin" not in source, (
        "Prompt-boundary dispatch ownership must remain forbidden."
    )


def test_runtime_has_no_dispatch_special_frame_matches_left() -> None:
    source = "\n".join(
        _runtime_source(path)
        for path in (
            VM_RS,
            VM_DISPATCH_RS,
            VM_STEP_RS,
            CORE_ROOT / "src/continuation.rs",
        )
        if path.exists()
    )
    assert "Frame::HandlerDispatch" not in source
    assert "Frame::DispatchOrigin" not in source
    banned_segment_accesses = (
        "seg.handler_dispatch",
        "segment.handler_dispatch",
        "handler_seg.handler_dispatch",
        "snapshot.handler_dispatch",
        "seg.dispatch_origin",
        "segment.dispatch_origin",
        "handler_seg.dispatch_origin",
        "snapshot.dispatch_origin",
        "seg.dispatch_id",
        "segment.dispatch_id",
        "current_seg.dispatch_id",
        "handler_seg.dispatch_id",
        "snapshot.dispatch_id",
        "exec_seg.dispatch_id",
    )
    for needle in banned_segment_accesses:
        assert needle not in source, (
            f"Pure stack-machine dispatch must not read or write Segment dispatch state via `{needle}`."
        )


def test_dispatch_cleanup_does_not_linearly_scan_all_segments() -> None:
    source = _vm_runtime_source()
    assert "fn remove_dispatch_origin" not in source, (
        "DispatchOrigin cleanup must not linearly scan the segment arena; it should be owned by "
        "the active handler segment and cleaned up structurally."
    )
    assert "dispatch_origin_segments:" not in source, (
        "DispatchOrigin ownership must be structural on the live handler segment, not tracked "
        "through a VM-level dispatch_id -> segment side map."
    )


def test_vm_runtime_has_no_parent_chain_completion_inference() -> None:
    source = _vm_runtime_source()

    banned = (
        "check_dispatch_completion",
        "mark_dispatch_completed",
        "mark_dispatch_threw",
        "lazy_pop_completed",
        "k_current.parent",
    )
    for needle in banned:
        assert needle not in source, f"dispatch completion/routing must not depend on `{needle}`"
