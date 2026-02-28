from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.cli

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_cli(
    *args: str,
    input_text: str | None = None,
    env_overrides: dict[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = ["uv", "run", "python", "-m", "doeff", "run", "--no-runbox", *args]
    env = {
        "PYTHONPATH": str(PROJECT_ROOT),
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DOEFF_DISABLE_DEFAULT_ENV": "1",
        "DOEFF_DISABLE_PROFILE": "1",
    }
    if env_overrides:
        for key, value in env_overrides.items():
            if value is None:
                env.pop(key, None)
            else:
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


def parse_json(output: str) -> dict[str, object]:
    try:
        return json.loads(output.strip())
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Expected JSON output, got: {output!r}") from exc


def test_program_error_includes_effect_context() -> None:
    result = run_cli(
        "-c",
        """
from doeff import Ask, do
@do
def failing():
    _ = yield Ask("missing_key")
    return 42
failing()
""",
        "--interpreter",
        "tests.cli.cli_assets.sync_interpreter",
        "--format",
        "json",
    )

    assert result.returncode == 1
    payload = parse_json(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"] == "MissingEnvKeyError"
    assert "traceback" in payload
    traceback = str(payload["traceback"])
    assert "MissingEnvKeyError" in traceback
    assert "missing_key" in traceback


def test_doeff_config_error_reported(tmp_path: Path) -> None:
    bad_config = tmp_path / ".doeff.py"
    bad_config.write_text("raise RuntimeError('bad config')\n", encoding="utf-8")

    result = run_cli(
        "--program",
        "tests.cli.cli_assets.sample_program",
        "--interpreter",
        "tests.cli.cli_assets.sync_interpreter",
        "--format",
        "json",
        env_overrides={
            "HOME": str(tmp_path),
            "DOEFF_DISABLE_DEFAULT_ENV": None,
        },
    )

    assert result.returncode == 1
    assert "Error executing ~/.doeff.py" in result.stderr
    payload = parse_json(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"] == "RuntimeError"
    assert "bad config" in str(payload["message"])


def test_handler_error_shows_handler_context() -> None:
    result = run_cli(
        "-c",
        """
from dataclasses import dataclass
from doeff import Delegate, Effect, EffectBase, Program, WithHandler, do

@dataclass(frozen=True, kw_only=True)
class Boom(EffectBase):
    pass

@do
def bad_handler(effect: Effect, _k):
    if isinstance(effect, Boom):
        raise RuntimeError("handler exploded")
    yield Delegate()

@do
def body() -> Program[int]:
    yield Boom()
    return 1

WithHandler(bad_handler, body())
""",
        "--interpreter",
        "tests.cli.cli_assets.sync_interpreter",
        "--format",
        "json",
    )

    assert result.returncode == 1
    payload = parse_json(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"] == "RuntimeError"
    traceback = str(payload.get("traceback", ""))
    assert "handler exploded" in traceback
    assert "bad_handler" in traceback


def test_json_error_has_structured_fields() -> None:
    result = run_cli(
        "--program",
        "nonexistent.module",
        "--interpreter",
        "tests.cli.cli_assets.sync_interpreter",
        "--format",
        "json",
    )

    assert result.returncode == 1
    payload = parse_json(result.stdout)
    assert payload["status"] == "error"
    assert isinstance(payload["error"], str)
    assert isinstance(payload["message"], str)
    assert "traceback" in payload
    assert isinstance(payload["traceback"], str)


def test_text_error_goes_to_stderr() -> None:
    result = run_cli(
        "--program",
        "nonexistent.module",
        "--interpreter",
        "tests.cli.cli_assets.sync_interpreter",
    )

    assert result.returncode == 1
    assert result.stdout.strip() == ""
    assert result.stderr.strip()
    assert "Traceback (most recent call last):" in result.stderr


def test_disable_default_env_flag(tmp_path: Path) -> None:
    bad_config = tmp_path / ".doeff.py"
    bad_config.write_text("raise RuntimeError('bad config')\n", encoding="utf-8")

    result = run_cli(
        "--program",
        "tests.cli.cli_assets.sample_program",
        "--interpreter",
        "tests.cli.cli_assets.sync_interpreter",
        "--format",
        "json",
        env_overrides={
            "HOME": str(tmp_path),
            "DOEFF_DISABLE_DEFAULT_ENV": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = parse_json(result.stdout)
    assert payload["status"] == "ok"
    assert payload["result"] == 5
    assert "Error executing ~/.doeff.py" not in result.stderr


def test_cli_json_uses_doeff_traceback() -> None:
    result = run_cli(
        "-c",
        "from doeff import Ask, do\n@do\ndef f():\n    yield Ask('x')\n    return 1\nf()",
        "--interpreter",
        "tests.cli.cli_assets.sync_interpreter",
        "--format",
        "json",
    )

    assert result.returncode == 1
    payload = parse_json(result.stdout)
    assert "doeff Traceback" in str(payload.get("traceback", ""))


def test_cli_text_uses_doeff_traceback() -> None:
    result = run_cli(
        "-c",
        "from doeff import Ask, do\n@do\ndef f():\n    yield Ask('x')\n    return 1\nf()",
        "--interpreter",
        "tests.cli.cli_assets.sync_interpreter",
    )

    assert result.returncode == 1
    assert "doeff Traceback" in result.stderr
