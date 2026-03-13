from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
VM_RS = CORE_ROOT / "src" / "vm.rs"
VM_STEP_RS = CORE_ROOT / "src" / "vm" / "step.rs"
VM_DISPATCH_RS = CORE_ROOT / "src" / "vm" / "dispatch.rs"
VM_TRACE_RS = CORE_ROOT / "src" / "vm" / "vm_trace.rs"


def test_vm_runtime_split_into_focused_modules() -> None:
    source = VM_RS.read_text(encoding="utf-8")

    assert '#[path = "vm/step.rs"]' in source
    assert '#[path = "vm/dispatch.rs"]' in source
    assert '#[path = "vm/vm_trace.rs"]' in source
    assert VM_STEP_RS.exists(), "step execution should live in src/vm/step.rs"
    assert VM_DISPATCH_RS.exists(), "dispatch logic should live in src/vm/dispatch.rs"
    assert VM_TRACE_RS.exists(), "trace/debug helpers should live in src/vm/vm_trace.rs"
