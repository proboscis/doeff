from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]


def _semgrep_rule_ids(config: Path, target: str, *, cwd: Path) -> set[str]:
    semgrep_bin = shutil.which("semgrep")
    if semgrep_bin is None:
        pytest.skip("semgrep is not installed")

    completed = subprocess.run(
        [semgrep_bin, "--config", str(config), "--json", target],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode not in {0, 1}:
        raise AssertionError(
            f"semgrep failed:\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)
    return {result["check_id"] for result in payload.get("results", [])}


def _has_rule(check_ids: set[str], expected_rule_id: str) -> bool:
    suffix = f".{expected_rule_id}"
    return any(check_id == expected_rule_id or check_id.endswith(suffix) for check_id in check_ids)


def test_vm_failfast_rust_rules_detect_known_bad_examples() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/rust"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / "packages/doeff-vm/.semgrep.yaml",
        "packages/doeff-vm/src",
        cwd=fixture_root,
    )

    expected = {
        "no-bare-ok-in-handler-can-handle",
        "no-silent-if-let-current-segment",
        "no-bare-ok-in-traceback-build",
    }
    assert all(_has_rule(check_ids, rule_id) for rule_id in expected)


def test_vm_failfast_python_rules_detect_known_bad_examples() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / ".semgrep.yaml",
        "doeff",
        cwd=fixture_root,
    )

    expected = {
        "no-silent-except-in-traceback",
        "no-silent-except-return-none",
    }
    assert all(_has_rule(check_ids, rule_id) for rule_id in expected)
