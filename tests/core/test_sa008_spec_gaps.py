"""Failing tests for SA-008 spec-gap audit (Phase 4).

Each test maps to one SA-008 gap and is expected to fail on current code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doeff import run
from doeff._types_internal import EffectBase


ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_SA_008_G01_no_yielded_unknown_variant() -> None:
    src = _read(ROOT / "packages" / "doeff-vm" / "src" / "yielded.rs")
    assert "Unknown" not in src


def test_SA_008_G02_classifier_no_unknown_fallback_branch() -> None:
    src = _read(ROOT / "packages" / "doeff-vm" / "src" / "pyvm.rs")
    assert "Yielded::Unknown" not in src


def test_SA_008_G03_no_dothunk_export_alias() -> None:
    src = _read(ROOT / "doeff" / "program.py")
    assert "DoThunk =" not in src


def test_SA_008_G04_map_flatmap_not_generator_wrapped() -> None:
    src = _read(ROOT / "doeff" / "program.py")
    assert "GeneratorProgram(" not in src


def test_SA_008_G05_map_flatmap_runtime_not_unimplemented() -> None:
    src = _read(ROOT / "packages" / "doeff-vm" / "src" / "vm.rs")
    assert "Map/FlatMap DoCtrl runtime evaluation is not implemented yet" not in src


def test_SA_008_G06_standard_effect_parse_not_marker_based() -> None:
    src = _read(ROOT / "packages" / "doeff-vm" / "src" / "handler.rs")
    state_block = src.split("fn parse_state_python_effect", 1)[1].split(
        "fn parse_reader_python_effect", 1
    )[0]
    reader_block = src.split("fn parse_reader_python_effect", 1)[1].split(
        "fn parse_writer_python_effect", 1
    )[0]
    writer_block = src.split("fn parse_writer_python_effect", 1)[1].split("#[cfg(not(test))]", 1)[0]
    assert "__doeff_state_" not in state_block
    assert "__doeff_reader_" not in reader_block
    assert "__doeff_writer_" not in writer_block


def test_SA_008_G07_scheduler_parse_not_marker_based() -> None:
    src = _read(ROOT / "packages" / "doeff-vm" / "src" / "scheduler.rs")
    parse_block = src.split("fn parse_scheduler_python_effect", 1)[1].split(
        "fn extract_waitable", 1
    )[0]
    assert "__doeff_scheduler_" not in parse_block


def test_SA_008_G08_kpc_parse_not_shape_attribute_driven() -> None:
    src = _read(ROOT / "doeff" / "program.py")
    assert "_annotation_is_program" not in src
    assert "_annotation_is_effect" not in src


def test_SA_008_G09_runresult_surface_unified() -> None:
    rust_src = _read(ROOT / "packages" / "doeff-vm" / "src" / "pyvm.rs")
    py_src = _read(ROOT / "doeff" / "_types_internal.py")
    assert not ("class PyRunResult" in rust_src and "class RunResult" in py_src)


def test_SA_008_G10_unhandled_effect_raises_clear_python_exception() -> None:
    class _NeverHandled(EffectBase):
        pass

    with pytest.raises(TypeError, match=r"(?i)(UnhandledEffect|unhandled effect)"):
        run(_NeverHandled(), handlers=[])


def test_SA_008_G11_no_public_runtime_internal_export() -> None:
    src = _read(ROOT / "packages" / "doeff-vm" / "doeff_vm" / "__init__.py")
    assert "PyVM" not in src
