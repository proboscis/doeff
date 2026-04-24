"""Failing tests for SA-007 spec-gap audit (Phase 4).

Each test maps to one SA-007 gap ID and is expected to fail on current code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doeff import Ask, Delegate, Effect, Resume, WithHandler, default_handlers, do, run
# REMOVED: from doeff.program import GeneratorProgram


ROOT = Path(__file__).resolve().parents[2]
VM_SRC = ROOT / "packages" / "doeff-vm" / "src"
VM_CORE_SRC = ROOT / "packages" / "doeff-vm-core" / "src"


def _read(path: Path) -> str:
    if not path.exists() and path.parent == VM_SRC:
        core_path = VM_CORE_SRC / path.name
        if core_path.exists():
            path = core_path
    return path.read_text(encoding="utf-8")


def _mk_program(value: int = 42) -> GeneratorProgram[int]:
    def gen():
        return value
        yield  # noqa: RET504

    return GeneratorProgram(gen)





def test_SA_007_G04_dothunk_removed_from_public_hierarchy() -> None:
    src = _read(ROOT / "doeff" / "program.py")
    assert "class DoThunk" not in src




def test_SA_007_G07_get_handlers_preserves_identity_not_placeholder() -> None:
    value_src = _read(ROOT / "packages" / "doeff-vm" / "src" / "value.rs")
    cont_src = _read(ROOT / "packages" / "doeff-vm" / "src" / "continuation.rs")
    assert '"rust_program_handler"' not in value_src
    assert '"rust_program_handler"' not in cont_src


def test_SA_007_G08_classifier_avoids_concrete_doctrl_type_checks() -> None:
    src = _read(ROOT / "packages" / "doeff-vm" / "src" / "pyvm.rs")
    assert "is_instance_of::<PyWithHandler>" not in src
    assert "is_instance_of::<PyResume>" not in src
    assert "is_instance_of::<PyDelegate>" not in src
    assert "is_instance_of::<PyTransfer>" not in src


def test_SA_007_G03_resume_constructor_validates_k_handle() -> None:
    with pytest.raises(TypeError, match=r"(?i)k"):
        Resume("not_k", Ask("x"))
