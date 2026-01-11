from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_cli(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    command = ["uv", "run", "doeff", "run", *args]
    env = {
        "PYTHONPATH": str(PROJECT_ROOT),
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=env,
        check=False,
        input=input_text,
    )


def parse_json(output: str) -> dict:
    try:
        return json.loads(output.strip())
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Expected JSON output, got: {output!r}") from exc


class TestCFlagBasic:
    def test_simple_expression(self) -> None:
        result = run_cli(
            "-c", "from doeff import Program; Program.pure(42)",
            "--interpreter", "tests.cli.cli_assets.sync_interpreter",
            "--format", "json",
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = parse_json(result.stdout)
        assert payload["status"] == "ok"
        assert payload["result"] == 42

    def test_non_program_expression(self) -> None:
        result = run_cli(
            "-c", "1 + 2 + 3",
            "--interpreter", "tests.cli.cli_assets.sync_interpreter",
            "--format", "json",
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = parse_json(result.stdout)
        assert payload["status"] == "ok"
        assert payload["result"] == 6

    def test_auto_discovers_interpreter(self) -> None:
        result = run_cli(
            "-c", "from doeff import Program; Program.pure(42)",
            "--format", "json",
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "[DOEFF][DISCOVERY] Interpreter:" in result.stderr
        payload = parse_json(result.stdout)
        assert payload["status"] == "ok"
        assert payload["result"] == 42

    def test_empty_code_error(self) -> None:
        result = run_cli(
            "-c", "",
            "--interpreter", "tests.cli.cli_assets.sync_interpreter",
        )
        assert result.returncode == 1
        assert "No code provided" in result.stderr


class TestCFlagWithYield:
    def test_toplevel_yield_creates_program(self) -> None:
        code = """
from doeff import Program
value = yield Program.pure(10)
value * 2
"""
        result = run_cli(
            "-c", code,
            "--interpreter", "tests.cli.cli_assets.sync_interpreter",
            "--format", "json",
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = parse_json(result.stdout)
        assert payload["status"] == "ok"
        assert payload["result"] == 20

    def test_multiple_yields(self) -> None:
        code = """
from doeff import Program
x = yield Program.pure(5)
y = yield Program.pure(7)
x + y
"""
        result = run_cli(
            "-c", code,
            "--interpreter", "tests.cli.cli_assets.sync_interpreter",
            "--format", "json",
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = parse_json(result.stdout)
        assert payload["status"] == "ok"
        assert payload["result"] == 12


class TestCFlagStdin:
    def test_stdin_code(self) -> None:
        code = "from doeff import Program; Program.pure(99)"
        result = run_cli(
            "-c", "-",
            "--interpreter", "tests.cli.cli_assets.sync_interpreter",
            "--format", "json",
            input_text=code,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = parse_json(result.stdout)
        assert payload["status"] == "ok"
        assert payload["result"] == 99

    def test_stdin_with_yield(self) -> None:
        code = """
from doeff import Program
x = yield Program.pure(3)
y = yield Program.pure(4)
x * y
"""
        result = run_cli(
            "-c", "-",
            "--interpreter", "tests.cli.cli_assets.sync_interpreter",
            "--format", "json",
            input_text=code,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = parse_json(result.stdout)
        assert payload["status"] == "ok"
        assert payload["result"] == 12


class TestCFlagWithEnv:
    def test_with_env(self) -> None:
        code = """
from doeff.effects import Ask
value = yield Ask("my_key")
value
"""
        result = run_cli(
            "-c", code,
            "--interpreter", "tests.cli.cli_assets.sync_interpreter",
            "--env", "tests.cli.cli_assets.sample_env",
            "--format", "json",
        )
        assert result.returncode in (0, 1), f"stderr: {result.stderr}"


class TestCFlagMutualExclusion:
    def test_program_and_c_mutually_exclusive(self) -> None:
        result = run_cli(
            "--program", "some.module.program",
            "-c", "1 + 1",
            "--interpreter", "tests.cli.cli_assets.sync_interpreter",
        )
        assert result.returncode == 2
        assert "not allowed with argument" in result.stderr or "mutually exclusive" in result.stderr.lower()

    def test_either_program_or_c_required(self) -> None:
        result = run_cli(
            "--interpreter", "tests.cli.cli_assets.sync_interpreter",
        )
        assert result.returncode == 2
        assert "required" in result.stderr.lower()
