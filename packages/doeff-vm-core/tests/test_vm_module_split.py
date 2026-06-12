"""Guard-layer test: VM implementation is split into focused sub-modules.

vm_trace.rs was removed in prior refactors — tracing is now derived from
the fiber chain walk (SPEC-VM-020).  The surviving modules are step.rs,
dispatch.rs, and handler.rs.
"""

from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[1]
VM_RS = CORE_ROOT / "src" / "vm.rs"
VM_STEP_RS = CORE_ROOT / "src" / "vm" / "step.rs"
VM_DISPATCH_RS = CORE_ROOT / "src" / "vm" / "dispatch.rs"
VM_HANDLER_RS = CORE_ROOT / "src" / "vm" / "handler.rs"


def test_vm_runtime_split_into_focused_modules() -> None:
    source = VM_RS.read_text(encoding="utf-8")

    assert '#[path = "vm/step.rs"]' in source
    assert '#[path = "vm/dispatch.rs"]' in source
    assert '#[path = "vm/handler.rs"]' in source
    assert VM_STEP_RS.exists(), "step execution should live in src/vm/step.rs"
    assert VM_DISPATCH_RS.exists(), "dispatch logic should live in src/vm/dispatch.rs"
    assert VM_HANDLER_RS.exists(), "handler lookup should live in src/vm/handler.rs"
