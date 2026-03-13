from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VM_DISPATCH = REPO_ROOT / "packages/doeff-vm-core/src/vm/dispatch.rs"
VM_STEP = REPO_ROOT / "packages/doeff-vm-core/src/vm/step.rs"
VM_TRACE = REPO_ROOT / "packages/doeff-vm-core/src/vm/vm_trace.rs"


def test_vm_trace_observer_removes_inline_trace_state_emits_from_vm_execution() -> None:
    for path in (VM_DISPATCH, VM_STEP, VM_TRACE):
        src = path.read_text()
        assert "trace_state.emit_" not in src, (
            f"VM-TRACE-OBSERVER-001 FAIL: {path.relative_to(REPO_ROOT)} "
            "must not call trace_state.emit_* directly"
        )


def test_vm_trace_observer_flushes_events_at_step_boundary() -> None:
    step_src = VM_STEP.read_text()
    assert "observe_pending_trace_events" in step_src, (
        "VM-TRACE-OBSERVER-001 FAIL: VM::step must flush pending trace events "
        "through the trace observer boundary"
    )
