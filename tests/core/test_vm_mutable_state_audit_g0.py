from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
VM_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "vm.rs"
STEP_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "vm" / "step.rs"
DISPATCH_RS = ROOT / "packages" / "doeff-vm-core" / "src" / "vm" / "dispatch.rs"
PYVM_RS = ROOT / "packages" / "doeff-vm" / "src" / "pyvm.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    runtime_source, _, _ = source.rpartition("\n#[cfg(test)]")
    return runtime_source if runtime_source else source


def _struct_body(source: str, name: str) -> str:
    match = re.search(rf"pub struct {name}\s*\{{(?P<body>.*?)\n\}}", source, re.DOTALL)
    assert match is not None, f"missing pub struct {name}"
    return match.group("body")


def test_g0_vm_has_no_signal_or_diagnostic_register_fields() -> None:
    body = _struct_body(_runtime_source(VM_RS), "VM")

    assert "pub mode:" not in body, "G0: step signal must not be stored on VM"
    assert "last_error_context:" not in body, (
        "G0: diagnostic context must travel through Signal/StepResult, not VM"
    )
    assert "pending_external:" not in body, (
        "G0: external-call continuation state must travel through StepResult, not VM"
    )


def test_g0_step_accepts_signal_and_returns_next_signal_explicitly() -> None:
    source = _runtime_source(STEP_RS)

    assert "pub fn step(&mut self, signal: Signal) -> StepResult" in source
    assert "StepResult::Continue(Signal::" in source
    assert "self.mode" not in source
    assert "last_error_context" not in source


def test_g0_dispatch_and_pyvm_do_not_touch_vm_registers() -> None:
    dispatch_source = _runtime_source(DISPATCH_RS)
    pyvm_source = _runtime_source(PYVM_RS)

    assert "self.mode" not in dispatch_source
    assert "last_error_context" not in dispatch_source
    assert ".mode =" not in pyvm_source
    assert "last_error_context" not in pyvm_source
    assert "receive_external_result" not in pyvm_source
