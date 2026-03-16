from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
TRACE_STATE_RS = CORE_ROOT / "src/trace_state.rs"
CAPTURE_RS = CORE_ROOT / "src/capture.rs"
VM_RS = CORE_ROOT / "src/vm.rs"
VM_TRACE_RS = CORE_ROOT / "src/vm/vm_trace.rs"
VM_DISPATCH_RS = CORE_ROOT / "src/vm/dispatch.rs"
VM_STEP_RS = CORE_ROOT / "src/vm/step.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    return source.split("#[cfg(test)]", 1)[0]


def _vm_runtime_source() -> str:
    return "\n".join(
        _runtime_source(path)
        for path in (VM_RS, VM_TRACE_RS, VM_DISPATCH_RS, VM_STEP_RS)
        if path.exists()
    )


def test_trace_state_has_no_active_chain_assembly_state_wrapper() -> None:
    source = _runtime_source(TRACE_STATE_RS)

    assert "ActiveChainAssemblyState" not in source, (
        "Frame-based traceback state must live directly on TraceState/frame snapshots, "
        "not behind an event-driven ActiveChainAssemblyState wrapper."
    )


def test_dispatch_display_lives_on_frame_snapshots_not_frame_dispatch_side_map() -> None:
    source = _runtime_source(TRACE_STATE_RS)

    assert "dispatch_display:" in source, (
        "ActiveChainFrameState must own dispatch display metadata directly."
    )
    assert "frame_dispatch:" not in source, (
        "Dispatch/frame association must be structural on the frame snapshot itself, "
        "not a side map."
    )


def test_vm_runtime_has_no_buffered_capture_events() -> None:
    source = _vm_runtime_source()

    assert "pending_trace_events" not in source, (
        "Traceback assembly must not depend on a VM-local buffered CaptureEvent queue."
    )
    assert "flush_trace_events" not in source, (
        "Frame-based traceback updates should happen directly at the mutation site, "
        "not via a later flush pass."
    )
    assert "CaptureEvent::" not in source, (
        "VM runtime should update trace state directly instead of constructing CaptureEvent "
        "objects and replaying them later."
    )


def test_capture_module_has_no_capture_event_enum() -> None:
    source = _runtime_source(CAPTURE_RS)

    assert "pub enum CaptureEvent" not in source, (
        "CaptureEvent should be removed once traceback state is updated directly."
    )
