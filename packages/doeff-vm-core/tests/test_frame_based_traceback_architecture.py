"""Guard-layer tests: frame-based traceback (no buffered capture events).

Tests that referenced removed files (trace_state.rs, capture.rs) were
deleted — those modules no longer exist, so the "no side-table" invariants
they guarded are vacuously satisfied.  The surviving test asserts the VM
runtime does not regress to buffered CaptureEvent replay.
"""

from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[1]
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
