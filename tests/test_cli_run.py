from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "doeff", "run", *args]
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        f"{PROJECT_ROOT}" + (os.pathsep + env["PYTHONPATH"]) if "PYTHONPATH" in env else str(PROJECT_ROOT)
    )
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=env,
        check=False,
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
