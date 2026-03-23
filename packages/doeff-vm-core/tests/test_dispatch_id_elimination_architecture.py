from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
IDS_RS = CORE_ROOT / "src/ids.rs"
FRAME_RS = CORE_ROOT / "src/frame.rs"
TRACE_STATE_RS = CORE_ROOT / "src/trace_state.rs"
VM_RS = CORE_ROOT / "src/vm.rs"
VM_DISPATCH_RS = CORE_ROOT / "src/vm/dispatch.rs"
VM_STEP_RS = CORE_ROOT / "src/vm/step.rs"
VM_TRACE_RS = CORE_ROOT / "src/vm/vm_trace.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    return source.split("#[cfg(test)]", 1)[0]


def _vm_runtime_source() -> str:
    return "\n".join(
        _runtime_source(path)
        for path in (VM_RS, VM_DISPATCH_RS, VM_STEP_RS, VM_TRACE_RS)
        if path.exists()
    )


def test_ids_runtime_has_no_dispatch_id_type() -> None:
    source = _runtime_source(IDS_RS)

    assert "pub struct DispatchId" not in source, (
        "SPEC-VM-020 Phase 1b: DispatchId type must be removed from ids.rs."
    )
    assert "impl DispatchId" not in source, (
        "SPEC-VM-020 Phase 1b: DispatchId constructor/helpers must be removed with the type."
    )


def test_frame_runtime_has_no_dispatch_id_or_dispatch_trace() -> None:
    source = _runtime_source(FRAME_RS)

    assert "pub struct DispatchTrace" not in source, (
        "SPEC-VM-020 Phase 1b: DispatchTrace must be removed; traceback is derived from the "
        "fiber chain walk."
    )
    assert "dispatch_id:" not in source, (
        "SPEC-VM-020 Phase 1b: ProgramDispatch must not retain a dispatch identity field."
    )
    assert "parent_dispatch_id:" not in source, (
        "SPEC-VM-020 Phase 1b: nested dispatch ancestry must be derived structurally, not by id."
    )


def test_trace_state_runtime_has_no_preserved_dispatch_side_buffers() -> None:
    source = _runtime_source(TRACE_STATE_RS)

    banned = (
        "preserved_error_frames",
        "preserved_thrown_dispatches",
        "finish_dispatch(",
    )
    for needle in banned:
        assert needle not in source, (
            "SPEC-VM-020 Phase 1b: TraceState must stop accumulating dispatch/error side state; "
            f"found `{needle}`."
        )


def test_vm_runtime_has_no_dispatch_id_lookup_or_completion_helpers() -> None:
    source = _vm_runtime_source()

    banned = (
        "dispatch_origin_for_dispatch_id(",
        "dispatch_origin_for_dispatch_id_anywhere(",
        "finish_dispatch_tracking(",
        "current_dispatch_id(",
    )
    for needle in banned:
        assert needle not in source, (
            "SPEC-VM-020 Phase 1b: dispatch identity/completion helpers must be removed; "
            f"found `{needle}`."
        )


def test_vm_core_runtime_has_no_dispatch_id_token_anywhere() -> None:
    runtime_sources = [
        _runtime_source(path)
        for path in CORE_ROOT.joinpath("src").rglob("*.rs")
        if path.is_file()
    ]
    combined = "\n".join(runtime_sources)
    assert "DispatchId" not in combined, (
        "SPEC-VM-020 Phase 1b acceptance: grep over packages/doeff-vm-core/src must not find "
        "DispatchId."
    )
