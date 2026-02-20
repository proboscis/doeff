"""Correctness-first P0 guard tests (strict boundary, no duck fallback)."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_p0_pyvm_to_generator_strict_has_no_duck_paths() -> None:
    src = _read("packages/doeff-vm/src/pyvm.rs")
    assert 'get_type().getattr("to_generator")' not in src
    assert 'hasattr("send")' not in src
    assert 'hasattr("throw")' not in src


def test_p0_handler_candidate_has_no_shape_heuristics() -> None:
    src = _read("packages/doeff-vm/src/handler.rs")
    assert "fn is_do_expr_candidate" not in src
    block = src
    assert 'hasattr("to_generator")' not in block
    assert 'getattr ("handler")' not in block
    assert 'getattr ("program")' not in block
    assert 'getattr ("continuation")' not in block


def test_p0_scheduler_has_no_field_name_fallbacks() -> None:
    src = _read("packages/doeff-vm/src/scheduler.rs")
    parse_start = src.index("fn parse_scheduler_python_effect")
    parse_end = src.index("fn extract_waitable", parse_start)
    block = src[parse_start:parse_end]
    assert '.or_else(|_| obj.getattr ("items"))' not in block
    assert 'obj.getattr ("task_id")' not in block


def test_p0_python_run_boundary_no_to_generator_duck_accept() -> None:
    src = _read("doeff/rust_vm.py")
    assert 'inspect.getattr_static(program, "to_generator", None)' not in src
    assert "if callable(to_gen):" not in src
