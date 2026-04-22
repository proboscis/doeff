"""Integration tests for ``doeff run --hy``.

The ``--hy`` source form accepts a block of Hy expressions, auto-wraps the
body in ``do!``, evaluates it, and runs the resulting Program directly —
users compose their own handler stack inside the snippet, so
``--interpreter``/``--env``/``--set``/``--apply``/``--transform`` are
rejected alongside ``--hy``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.cli

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_cli(
    *args: str, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    command = ["uv", "run", "python", "-m", "doeff", "run", *args]
    env = {
        "PYTHONPATH": str(PROJECT_ROOT),
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DOEFF_DISABLE_DEFAULT_ENV": "1",
    }
    for key in ("UV_PROJECT_ENVIRONMENT", "UV_CACHE_DIR", "VIRTUAL_ENV"):
        value = os.environ.get(key)
        if value:
            env[key] = value
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


class TestHyFlagBasic:
    def test_pure_program(self) -> None:
        result = run_cli("--hy", "(import doeff [Pure]) (Pure 42)", "--format", "json")
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = parse_json(result.stdout)
        assert payload["status"] == "ok"
        assert payload["result"] == 42

    def test_bind_with_arrow(self) -> None:
        source = (
            "(import doeff [Pure]) "
            "(do! (<- x (Pure 10)) (<- y (Pure 32)) (+ x y))"
        )
        result = run_cli("--hy", source, "--format", "json")
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = parse_json(result.stdout)
        assert payload["result"] == 42

    def test_handle_macro_inline(self) -> None:
        source = """
(import doeff [Ask])

(handle
  (do!
    (<- v (Ask "answer"))
    v)
  (Ask [key]
    (resume (get {"answer" 42} key))))
"""
        result = run_cli("--hy", source, "--format", "json")
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = parse_json(result.stdout)
        assert payload["result"] == 42

    def test_defhandler_then_with_handler(self) -> None:
        source = """
(import doeff [Ask WithHandler])

(defhandler ask-env
  (Ask [key]
    (resume (get {"answer" 42} key))))

(WithHandler ask-env
  (do!
    (<- v (Ask "answer"))
    v))
"""
        result = run_cli("--hy", source, "--format", "json")
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = parse_json(result.stdout)
        assert payload["result"] == 42

    def test_reads_stdin_with_dash(self) -> None:
        source = "(import doeff [Pure]) (Pure 99)"
        result = run_cli("--hy", "-", "--format", "json", input_text=source)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = parse_json(result.stdout)
        assert payload["result"] == 99


class TestHyFlagExclusivity:
    @pytest.mark.parametrize(
        "conflict",
        [
            ("--interpreter", "tests.cli.cli_assets.sync_interpreter"),
            ("--env", "tests.cli.cli_assets.sample_env"),
            ("--set", "foo=bar"),
            ("--apply", "tests.cli.cli_assets.add_three"),
            ("--transform", "tests.cli.cli_assets.add_three"),
        ],
    )
    def test_rejects_conflicting_flag(self, conflict: tuple[str, str]) -> None:
        result = run_cli("--hy", "(Pure 1)", *conflict, "--format", "json")
        assert result.returncode != 0
        assert "--hy" in (result.stderr + result.stdout)

    def test_rejects_program(self) -> None:
        # --program and --hy share the source-group; argparse handles this.
        result = run_cli(
            "--hy",
            "(Pure 1)",
            "--program",
            "tests.cli.cli_assets.sample_program",
        )
        assert result.returncode != 0


class TestHyFlagErrors:
    def test_syntax_error_reports_clearly(self) -> None:
        result = run_cli("--hy", "(unclosed", "--format", "json")
        assert result.returncode != 0
        combined = result.stderr + result.stdout
        assert "hy" in combined.lower() or "syntax" in combined.lower() or "parse" in combined.lower()

    def test_empty_source_fails(self) -> None:
        result = run_cli("--hy", "   ", "--format", "json")
        assert result.returncode != 0
