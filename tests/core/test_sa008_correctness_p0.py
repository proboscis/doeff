"""Correctness-first P0 guard tests (strict boundary, no duck fallback)."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    path = ROOT / rel
    if not path.exists():
        fallback_rel = {
            "packages/doeff-vm/src/effect.rs": "packages/doeff-core-effects/src/effects/mod.rs",
            "packages/doeff-vm/src/handler.rs": "packages/doeff-core-effects/src/handlers/mod.rs",
            "packages/doeff-vm/src/scheduler.rs": "packages/doeff-core-effects/src/scheduler/mod.rs",
        }.get(rel)
        if fallback_rel is not None:
            path = ROOT / fallback_rel
    return path.read_text(encoding="utf-8")


def test_p0_pyvm_to_generator_strict_has_no_duck_paths() -> None:
    src = _read("packages/doeff-vm/src/pyvm.rs")
    assert 'getattr("to_generator")' not in src
    assert 'get_type().getattr("to_generator")' not in src
    assert 'hasattr("send")' not in src
    assert 'hasattr("throw")' not in src


def test_p0_pyvm_has_no_python_package_imports_or_traceback_attr_probing() -> None:
    src = _read("packages/doeff-vm/src/pyvm.rs")
    assert 'py.import("doeff.errors")' not in src
    assert 'getattr("active_chain")' not in src
    assert 'getattr("entries")' not in src
