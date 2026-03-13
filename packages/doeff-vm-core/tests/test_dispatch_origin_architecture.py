from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
FRAME_RS = CORE_ROOT / "src/frame.rs"
VM_RS = CORE_ROOT / "src/vm.rs"
CORE_LIB_RS = CORE_ROOT / "src/lib.rs"
DISPATCH_RS = CORE_ROOT / "src/dispatch.rs"
DISPATCH_STATE_RS = CORE_ROOT / "src/dispatch_state.rs"
VM_BINDINGS_LIB_RS = CORE_ROOT.parent / "doeff-vm" / "src/lib.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    return source.split("#[cfg(test)]", 1)[0]


def test_frame_dispatch_origin_is_runtime_dispatch_anchor() -> None:
    source = _runtime_source(FRAME_RS)
    assert "DispatchOrigin {" in source, (
        "Dispatch must be anchored in Frame::DispatchOrigin on the prompt boundary."
    )
    assert "dispatch_id: DispatchId" in source
    assert "effect: DispatchEffect" in source
    assert "k_origin: crate::continuation::Continuation" in source or "k_origin: Continuation" in source


def test_vm_runtime_has_no_dispatch_side_table_left() -> None:
    vm_source = _runtime_source(VM_RS)
    core_lib_source = _runtime_source(CORE_LIB_RS)
    bindings_lib_source = _runtime_source(VM_BINDINGS_LIB_RS)
    dispatch_source = _runtime_source(DISPATCH_RS)

    assert "dispatch_state:" not in vm_source, "VM must not own a dispatch_state side table."
    assert "DispatchState" not in vm_source
    assert "DispatchContext" not in vm_source
    assert "DispatchContext" not in core_lib_source
    assert "DispatchContext" not in bindings_lib_source
    assert "DispatchContext" not in dispatch_source
    assert not DISPATCH_STATE_RS.exists(), "dispatch_state.rs must be removed with the side table."


def test_vm_runtime_has_no_parent_chain_completion_inference() -> None:
    source = _runtime_source(VM_RS)

    banned = (
        "check_dispatch_completion",
        "mark_dispatch_completed",
        "mark_dispatch_threw",
        "lazy_pop_completed",
        "k_current.parent",
    )
    for needle in banned:
        assert needle not in source, f"dispatch completion/routing must not depend on `{needle}`"
