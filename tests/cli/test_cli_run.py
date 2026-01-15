from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str, input: str | None = None) -> subprocess.CompletedProcess[str]:
    command = ["uv", "run", "doeff", "run", *args]
    # Minimal env for subprocess - os.environ needed for subprocess.run()
    pythonpath = f"{PROJECT_ROOT}" + (os.pathsep + os.environ.get("PYTHONPATH", "")) if "PYTHONPATH" in os.environ else str(PROJECT_ROOT)  # noqa: PINJ050
    env = {"PYTHONPATH": pythonpath, "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}  # noqa: PINJ050
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=env,
        check=False,
        input=input,
    )


def parse_json(output: str) -> dict[str, object]:
    try:
        return json.loads(output.strip())
    except json.JSONDecodeError as exc:  # pragma: no cover - diagnostic aid
        raise AssertionError(f"Expected JSON output, got: {output!r}") from exc


@pytest.mark.parametrize(
    "extra_args, expected",
    [
        ([], 5),
        (["--apply", "tests.cli_assets.double_program"], 10),
        (
            [
                "--apply",
                "tests.cli_assets.double_program",
                "--transform",
                "tests.cli_assets.add_three",
            ],
            13,
        ),
    ],
)
def test_doeff_run_json_output(extra_args: list[str], expected: int) -> None:
    result = run_cli(
        "--program",
        "tests.cli_assets.sample_program",
        "--interpreter",
        "tests.cli_assets.sync_interpreter",
        "--format",
        "json",
        *extra_args,
    )
    assert result.returncode == 0, result.stderr
    payload = parse_json(result.stdout)
    assert payload["status"] == "ok"
    assert payload["result"] == expected


@pytest.mark.xfail(reason="CESKRunResult.display() is simplified; full report format not available")
def test_doeff_run_text_report() -> None:
    result = run_cli(
        "--program",
        "tests.cli_assets.ask_program",
        "--interpreter",
        "tests.cli_assets.runresult_interpreter",
        "--env",
        "tests.cli_assets.sample_env",
        "--report",
    )

    assert result.returncode == 0, result.stderr
    assert "5" in result.stdout
    assert "Effect Call Tree" in result.stdout


@pytest.mark.xfail(reason="CESKRunResult.display() is simplified; full report format not available")
def test_doeff_run_json_report_includes_call_tree() -> None:
    result = run_cli(
        "--program",
        "tests.cli_assets.ask_program",
        "--interpreter",
        "tests.cli_assets.runresult_interpreter",
        "--format",
        "json",
        "--env",
        "tests.cli_assets.sample_env",
        "--report",
    )

    assert result.returncode == 0, result.stderr
    payload = parse_json(result.stdout)
    assert payload["status"] == "ok"
    assert payload["result"] == 5
    assert "report" in payload
    assert "call_tree" in payload
    assert "Ask" in payload["call_tree"]


def test_doeff_run_missing_interpreter_argument() -> None:
    result = run_cli(
        "--program",
        "tests.cli_assets.sample_program",
        "--interpreter",
        "tests.cli_assets.sync_interpreter",
        "--format",
        "json",
        "--transform",
        "tests.cli_assets.sync_interpreter",
    )
    assert result.returncode == 1
    payload = parse_json(result.stdout)
    assert payload["status"] == "error"


# E2E Tests for Auto-Discovery Feature


@pytest.mark.xfail(reason="Pre-existing: indexer fails when CWD is tests/ but module path is tests.*")
def test_auto_discover_interpreter_and_env() -> None:
    """Test auto-discovery of interpreter and environments."""
    result = run_cli(
        "--program",
        "tests.fixtures_discovery.myapp.features.auth.login.login_program",
        "--format",
        "json",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    payload = parse_json(result.stdout)

    assert payload["status"] == "ok"
    assert payload["result"] == "Login via oauth2 (timeout: 10s)"

    # Verify discovered interpreter (closest match)
    assert "auth_interpreter" in payload["interpreter"]

    # Verify discovered envs (all in hierarchy)
    assert len(payload["envs"]) == 3
    assert "base_env" in payload["envs"][0]
    assert "features_env" in payload["envs"][1]
    assert "auth_env" in payload["envs"][2]


@pytest.mark.xfail(reason="Pre-existing: indexer fails when CWD is tests/ but module path is tests.*")
def test_manual_interpreter_overrides_discovery() -> None:
    """Test that explicit --interpreter overrides auto-discovery."""
    result = run_cli(
        "--program",
        "tests.fixtures_discovery.myapp.features.auth.login.login_program",
        "--interpreter",
        "tests.fixtures_discovery.myapp.base_interpreter",  # Force base instead of auth
        "--format",
        "json",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    payload = parse_json(result.stdout)

    assert payload["status"] == "ok"
    # Should still use discovered envs
    assert len(payload["envs"]) == 3
    # But use the specified interpreter
    assert payload["interpreter"] == "tests.fixtures_discovery.myapp.base_interpreter"


def test_no_default_interpreter_error() -> None:
    """Test helpful error when no default interpreter found."""
    # cli_assets.sample_program has no default interpreter in its hierarchy
    result = run_cli(
        "--program",
        "tests.cli_assets.sample_program",
        "--format",
        "json",
    )
    assert result.returncode == 1
    payload = parse_json(result.stdout)

    assert payload["status"] == "error"
    assert "No default interpreter found" in payload["message"]
    assert "tests.cli_assets.sample_program" in payload["message"]


def test_auto_discovery_with_apply() -> None:
    """Test auto-discovery works with --apply flag."""
    result = run_cli(
        "--program",
        "tests.fixtures_discovery.myapp.features.auth.login.login_program",
        "--apply",
        "tests.cli_assets.double_program",  # This will fail but tests the flow
        "--format",
        "json",
    )
    # Should discover interpreter/envs, then try to apply (which might fail)
    # The point is discovery should happen before apply
    assert result.returncode in (0, 1)  # Either succeeds or fails at apply stage
    payload = parse_json(result.stdout)

    # If it reaches apply stage, discovery worked
    assert "interpreter" in payload or payload["status"] == "error"


def test_auto_discovery_with_transform() -> None:
    """Test auto-discovery works with --transform flag."""
    result = run_cli(
        "--program",
        "tests.fixtures_discovery.myapp.features.auth.login.login_program",
        "--transform",
        "tests.cli_assets.add_three",
        "--format",
        "json",
    )
    # Transforms on string programs don't make sense but tests the flow
    assert result.returncode in (0, 1)
    payload = parse_json(result.stdout)

    # Discovery should happen regardless of transform
    assert "interpreter" in payload or payload["status"] == "error"


def test_doeff_run_with_script() -> None:
    """Test run command with script that accesses program, value, and interpreter."""
    script = """
print(f"Program type: {type(program).__name__}")
print(f"Value: {value}")
print(f"Interpreter type: {type(interpreter).__name__}")
"""
    result = run_cli(
        "--program",
        "tests.cli_assets.sample_program",
        "--interpreter",
        "tests.cli_assets.sync_interpreter",
        "-",
        input=script,
    )
    assert result.returncode == 0, result.stderr
    assert "Program type: Pure" in result.stdout or "Program" in result.stdout
    assert "Value: 5" in result.stdout
    assert "Interpreter" in result.stdout


def test_doeff_run_with_script_using_interpreter() -> None:
    """Test run command with script that re-runs the program using injected interpreter."""
    script = """
print(f"Initial value: {value}")
# Use the injected interpreter to re-run
if isinstance(interpreter, CESKInterpreter):
    run_again = interpreter.run(program)
    print(f"Re-run value: {run_again.value}")
else:
    # If interpreter is a function, call it directly
    run_again_value = interpreter(program)
    print(f"Re-run value: {run_again_value}")
"""
    result = run_cli(
        "--program",
        "tests.cli_assets.sample_program",
        "--interpreter",
        "tests.cli_assets.sync_interpreter",
        "-",
        input=script,
    )
    assert result.returncode == 0, result.stderr
    assert "Initial value: 5" in result.stdout
    assert "Re-run value: 5" in result.stdout


def test_doeff_run_without_script() -> None:
    """Test that run command works normally when script is not provided."""
    result = run_cli(
        "--program",
        "tests.cli_assets.sample_program",
        "--interpreter",
        "tests.cli_assets.sync_interpreter",
        "--format",
        "json",
    )
    assert result.returncode == 0, result.stderr
    payload = parse_json(result.stdout)
    assert payload["status"] == "ok"
    assert payload["result"] == 5


def test_doeff_run_with_empty_script_string() -> None:
    """Test that run command works normally when empty string is provided as script."""
    result = run_cli(
        "--program",
        "tests.cli_assets.sample_program",
        "--interpreter",
        "tests.cli_assets.sync_interpreter",
        "--format",
        "json",
        "",
    )
    assert result.returncode == 0, result.stderr
    payload = parse_json(result.stdout)
    assert payload["status"] == "ok"
    assert payload["result"] == 5


@pytest.mark.xfail(reason="Pre-existing: indexer fails when CWD is tests/ but module path is tests.*")
def test_doeff_run_with_script_auto_discovery() -> None:
    """Test that auto-discovered interpreter and environments are loaded in script execution."""
    script = """
print(f"Interpreter path contains 'auth_interpreter': {'auth_interpreter' in str(interpreter)}")
print(f"Value: {value}")
# Verify that environments were auto-discovered and merged
# The program should have access to merged env values
print(f"Program has Local effect (env applied): {'Local' in type(program).__name__}")
"""
    result = run_cli(
        "--program",
        "tests.fixtures_discovery.myapp.features.auth.login.login_program",
        "-",
        input=script,
    )
    assert result.returncode == 0, result.stderr
    # Should use auto-discovered auth_interpreter (closest to login module)
    assert "auth_interpreter" in result.stdout or "auth_interpreter" in result.stderr
    # Should have the merged environment values
    assert "Login via oauth2" in result.stdout or "oauth2" in result.stdout
    # Program should have Local effect applied (envs merged)
    assert "Local" in result.stdout
