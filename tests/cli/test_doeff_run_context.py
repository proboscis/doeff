"""Tests for DoeffRunContext — CLI context passed to interpreters."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from doeff.cli.run_services import DoeffRunContext
from tests.cli.cli_child import with_settings

pytestmark = pytest.mark.cli

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# --- Unit tests (no VM needed) ---


def test_doeff_run_context_is_frozen():
    ctx = DoeffRunContext(
        program_ref="my.module.prog",
        interpreter_ref="my.module.interp",
        env_refs=["my.module.env"],
        set_overrides={"k": "v"},
        apply_refs=[],
        transform_refs=[],
    )
    with pytest.raises(AttributeError):
        ctx.program_ref = "other"  # type: ignore[misc]


def test_doeff_run_context_fields():
    ctx = DoeffRunContext(
        program_ref="a.b.c",
        interpreter_ref="x.y.z",
        env_refs=["e1", "e2"],
        set_overrides={"sim_start": "2026-01-01"},
        apply_refs=["a.b.apply_fn"],
        transform_refs=["t1"],
    )
    assert ctx.program_ref == "a.b.c"
    assert ctx.interpreter_ref == "x.y.z"
    assert ctx.env_refs == ["e1", "e2"]
    assert ctx.set_overrides == {"sim_start": "2026-01-01"}
    assert ctx.apply_refs == ["a.b.apply_fn"]
    assert ctx.transform_refs == ["t1"]


def test_doeff_run_context_equality():
    a = DoeffRunContext("a", "b", ["e"], {}, [], [])
    b = DoeffRunContext("a", "b", ["e"], {}, [], [])
    assert a == b


def test_doeff_run_context_exported_from_doeff():
    from doeff import DoeffRunContext as Exported
    assert Exported is DoeffRunContext


# --- E2E / integration tests (subprocess) ---


def _run_cli(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    command = ["uv", "run", "python", "-m", "doeff", "run", *args]
    settings = {"PYTHONPATH": str(PROJECT_ROOT), "DOEFF_DISABLE_DEFAULT_ENV": "1"}
    return subprocess.run(
        with_settings(command, settings), cwd=PROJECT_ROOT, text=True, capture_output=True,
        check=False, input=input_text,
    )
