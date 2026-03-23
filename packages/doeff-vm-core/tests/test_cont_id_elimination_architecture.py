from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
CORE_SRC = CORE_ROOT / "src"
FRAME_RS = CORE_SRC / "frame.rs"
CONTINUATION_RS = CORE_SRC / "continuation.rs"
VM_BINDINGS_RS = CORE_ROOT.parent / "doeff-vm" / "src" / "pyvm.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    return source.split("#[cfg(test)]", 1)[0]


def test_core_runtime_has_no_cont_id_token() -> None:
    runtime_sources = [
        _runtime_source(path)
        for path in CORE_SRC.rglob("*.rs")
        if path.is_file()
    ]
    combined = "\n".join(runtime_sources)
    assert "ContId" not in combined, (
        "SPEC-VM-020 Phase 2 acceptance: packages/doeff-vm-core/src must not retain ContId."
    )


def test_program_dispatch_uses_fiber_identity_not_origin_cont_id() -> None:
    source = _runtime_source(FRAME_RS)

    assert "origin_cont_id:" not in source, (
        "SPEC-VM-020 Phase 2: ProgramDispatch must use fiber-based identity, not origin_cont_id."
    )
    assert "parent_origin_cont_id:" not in source, (
        "SPEC-VM-020 Phase 2: nested dispatch ancestry must not be tracked with parent_origin_cont_id."
    )


def test_python_continuation_runtime_does_not_expose_cont_id() -> None:
    continuation_source = _runtime_source(CONTINUATION_RS)
    bindings_source = _runtime_source(VM_BINDINGS_RS)

    for needle in ('"cont_id"', "cont_id:", "cont_id.", "cont_id("):
        assert needle not in continuation_source, (
            "SPEC-VM-020 Phase 2: continuation.rs must stop exposing cont_id to Python."
        )
        assert needle not in bindings_source, (
            "SPEC-VM-020 Phase 2: pyvm.rs must stop exposing cont_id to Python."
        )
