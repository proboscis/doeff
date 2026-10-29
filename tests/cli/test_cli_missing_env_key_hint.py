"""`doeff run` adds a `--hy` hint to an Ask whose key the env does not hold.

The hint is the README's CLI form (compose lazy-ask with an env inline in `--hy`),
not the legacy `--set` / `--env` / `--interpreter` flags, and only the CLI gives it:
the error raised to Python names no CLI flag
(tests/effects/test_missing_env_key_message.py). The message keeps its newlines.

Card: doeff-publish 8 の誤りの表の 7 (e08_reader_missing_key).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.cli.cli_child import with_settings

pytestmark = pytest.mark.cli

PROJECT_ROOT = Path(__file__).resolve().parents[2]

READER_MISS = (
    "(import doeff-core-effects [Ask reader]) "
    '((reader :env {"greeting" "hello"}) (Ask "greting"))'
)

HY_HINT = (
    "doeff run --hy '(import myapp [p]) (import doeff-core-effects [lazy-ask])"
    ' ((lazy-ask :env {"greting" VALUE}) p)\''
)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    command = ["uv", "run", "python", "-m", "doeff", "run", "--no-runbox", *args]
    settings = {"PYTHONPATH": str(PROJECT_ROOT), "DOEFF_DISABLE_DEFAULT_ENV": "1"}
    return subprocess.run(
        with_settings(command, settings),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_text_error_keeps_newlines_and_gives_the_hy_hint():
    result = run_cli("--hy", READER_MISS)

    assert result.returncode == 1
    lines = result.stderr.splitlines()
    assert "Error: Ask: key not found: 'greting'" in lines
    assert "  Did you mean 'greeting'?" in lines
    assert "  Keys in the env: 'greeting'" in lines
    assert "\\n" not in result.stderr
    assert "From the CLI, compose the env inline with --hy (README, CLI):" in lines
    assert f"  {HY_HINT}" in lines
    for flag in ("--set", "--env ", "--interpreter"):
        assert flag not in result.stderr


def test_json_error_carries_the_hy_hint():
    result = run_cli("--format", "json", "--hy", READER_MISS)

    assert result.returncode == 1
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "error"
    assert payload["error"] == "MissingEnvKeyError"
    assert payload["message"].splitlines()[0] == "Ask: key not found: 'greting'"
    assert HY_HINT in payload["hint"]
