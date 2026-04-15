"""Tests for DoeffRunContext — CLI context passed to interpreters."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from doeff.cli.run_services import DoeffRunContext

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
    pythonpath = (
        f"{PROJECT_ROOT}" + (os.pathsep + os.environ.get("PYTHONPATH", ""))
        if "PYTHONPATH" in os.environ
        else str(PROJECT_ROOT)
    )
    env = {
        "PYTHONPATH": pythonpath,
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DOEFF_DISABLE_DEFAULT_ENV": "1",
    }
    for key in ("UV_PROJECT_ENVIRONMENT", "UV_CACHE_DIR", "VIRTUAL_ENV"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return subprocess.run(
        command, cwd=PROJECT_ROOT, text=True, capture_output=True,
        env=env, check=False, input=input_text,
    )


def test_ctx_passed_to_interpreter():
    """ctx_interpreter returns the DoeffRunContext as JSON-serializable output."""
    result = _run_cli(
        "--program", "tests.cli_assets.sample_program",
        "--interpreter", "tests.cli_assets.ctx_interpreter",
        "--format", "json",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "ok"
    # result is the DoeffRunContext repr
    ctx_repr = str(payload["result"])
    assert "tests.cli_assets.sample_program" in ctx_repr


def test_ctx_contains_env_refs_and_set_overrides():
    """ctx captures --env and --set from CLI invocation."""
    result = _run_cli(
        "--program", "tests.cli_assets.ask_program",
        "--interpreter", "tests.cli_assets.ctx_interpreter",
        "--env", "tests.cli_assets.sample_env",
        "--set", "value=42",
        "--format", "json",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "ok"
    ctx_repr = str(payload["result"])
    assert "tests.cli_assets.sample_env" in ctx_repr
    assert "value" in ctx_repr


def test_ctx_set_overrides_contains_raw_strings():
    """set_overrides must contain raw CLI strings, not resolved objects.

    When --set key={some.symbol} is used, the ctx passed to interpreters
    must preserve the raw '{some.symbol}' string so that the original
    doeff run command can be reconstructed for remote execution.
    """
    result = _run_cli(
        "--program", "tests.cli_assets.ask_program",
        "--interpreter", "tests.cli_assets.ctx_interpreter",
        "--env", "tests.cli_assets.sample_env",
        "--set", "obj={tests.cli_assets.sample_env}",
        "--set", "plain=hello",
        "--format", "json",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "ok"
    ctx_repr = str(payload["result"])
    # Raw brace syntax must be preserved in set_overrides
    assert "{tests.cli_assets.sample_env}" in ctx_repr
    # Plain string values stay as-is
    assert "'plain': 'hello'" in ctx_repr


def test_legacy_interpreter_works_without_ctx():
    """Existing interpreters without ctx= parameter still work."""
    result = _run_cli(
        "--program", "tests.cli_assets.sample_program",
        "--interpreter", "tests.cli_assets.sync_interpreter",
        "--format", "json",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "ok"
    assert payload["result"] == 5
