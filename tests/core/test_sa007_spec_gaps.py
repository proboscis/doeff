"""Failing tests for SA-007 spec-gap audit (Phase 4).

Each test maps to one SA-007 gap ID and is expected to fail on current code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doeff import Ask, Resume

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


def _mk_program(value: int = 42) -> GeneratorProgram[int]:  # noqa: F821 - legacy removed API reference is intentionally preserved
    def gen():
        return value
        yield

    return GeneratorProgram(gen)  # noqa: F821 - legacy removed API reference is intentionally preserved





def test_SA_007_G04_dothunk_removed_from_public_hierarchy() -> None:  # noqa: N802 - public or spec test name is intentionally stable
    src = _read(ROOT / "doeff" / "program.py")
    assert "class DoThunk" not in src




def test_SA_007_G07_get_handlers_preserves_identity_not_placeholder() -> None:  # noqa: N802 - public or spec test name is intentionally stable
    value_src = _read(ROOT / "packages" / "doeff-vm" / "src" / "value.rs")
    cont_src = _read(ROOT / "packages" / "doeff-vm" / "src" / "continuation.rs")
    assert '"rust_program_handler"' not in value_src
    assert '"rust_program_handler"' not in cont_src


def test_SA_007_G08_classifier_avoids_concrete_doctrl_type_checks() -> None:  # noqa: N802 - public or spec test name is intentionally stable
    src = _read(ROOT / "packages" / "doeff-vm" / "src" / "pyvm.rs")
    assert "is_instance_of::<PyWithHandler>" not in src
    assert "is_instance_of::<PyResume>" not in src
    assert "is_instance_of::<PyDelegate>" not in src
    assert "is_instance_of::<PyTransfer>" not in src


def test_SA_007_G03_resume_constructor_validates_k_handle() -> None:  # noqa: N802 - public or spec test name is intentionally stable
    with pytest.raises(TypeError, match=r"(?i)k"):
        Resume("not_k", Ask("x"))
