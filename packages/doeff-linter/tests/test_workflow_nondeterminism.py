import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
LINTER_DIR = ROOT / "packages" / "doeff-linter"
FIXTURES = LINTER_DIR / "tests" / "fixtures" / "workflow_nondeterminism"


def run_linter(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--",
            "--no-config",
            "--no-log",
            "--enable",
            "DOEFF032",
            "--output-format",
            "json",
            str(path),
        ],
        cwd=LINTER_DIR,
        text=True,
        capture_output=True,
        check=False,
    )


def parse_json_output(result: subprocess.CompletedProcess[str]) -> list[dict]:
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("fixture_name", "replacement"),
    [
        ("datetime_now.py", "time!"),
        ("datetime_today.py", "time!"),
        ("time_time.py", "time!"),
        ("time_monotonic.py", "time!"),
        ("random_call.py", "random!"),
        ("open_call.py", "gate!"),
        ("pathlib_write.py", "gate!"),
        ("subprocess_call.py", "gate!"),
        ("requests_import.py", "gate!"),
        ("httpx_import.py", "gate!"),
        ("socket_import.py", "gate!"),
        ("urllib_import.py", "gate!"),
        ("non_allowlisted_import.py", ":params"),
    ],
)
def test_workflow_nondeterminism_fixture_fires(
    fixture_name: str,
    replacement: str,
) -> None:
    result = run_linter(FIXTURES / fixture_name)

    assert result.returncode == 1, result.stderr
    payload = parse_json_output(result)
    assert [entry["rule"] for entry in payload] == ["DOEFF032"]
    assert payload[0]["severity"] == "error"
    assert replacement in payload[0]["fix"]


def test_clean_workflow_fixture_passes() -> None:
    result = run_linter(FIXTURES / "clean_workflow.py")

    assert result.returncode == 0, result.stderr
    assert parse_json_output(result) == []


def test_non_workflow_module_is_not_checked() -> None:
    result = run_linter(FIXTURES / "plain_module_with_random.py")

    assert result.returncode == 0, result.stderr
    assert parse_json_output(result) == []


def test_workflow_nondeterminism_has_no_file_allowlist() -> None:
    result = run_linter(FIXTURES / "file_noqa_workflow.py")

    assert result.returncode == 1, result.stderr
    payload = parse_json_output(result)
    assert [entry["rule"] for entry in payload] == ["DOEFF032"]
