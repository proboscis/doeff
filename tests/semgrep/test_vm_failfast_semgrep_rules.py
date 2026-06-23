from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]


def _semgrep_results(config: Path, target: str, *, cwd: Path) -> list[dict[str, Any]]:
    semgrep_bin = shutil.which("semgrep")
    if semgrep_bin is None:
        pytest.skip("semgrep is not installed")

    completed = subprocess.run(
        [semgrep_bin, "--no-git-ignore", "--config", str(config), "--json", target],
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
    return cast(list[dict[str, Any]], payload.get("results", []))


def _semgrep_rule_ids(config: Path, target: str, *, cwd: Path) -> set[str]:
    return {str(result["check_id"]) for result in _semgrep_results(config, target, cwd=cwd)}


def _has_rule(check_ids: set[str], expected_rule_id: str) -> bool:
    suffix = f".{expected_rule_id}"
    return any(check_id == expected_rule_id or check_id.endswith(suffix) for check_id in check_ids)


def _rule_start_lines(results: list[dict[str, Any]], expected_rule_id: str) -> set[int]:
    lines: set[int] = set()
    for result in results:
        check_id = str(result["check_id"])
        if not _has_rule({check_id}, expected_rule_id):
            continue
        start = cast(dict[str, int], result["start"])
        lines.add(start["line"])
    return lines


def test_vm_failfast_rust_rules_detect_known_bad_examples() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/rust"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / "packages/doeff-vm/.semgrep.yaml",
        "packages",
        cwd=fixture_root,
    )

    expected = {
        "no-bare-ok-in-handler-can-handle",
        "no-silent-if-let-current-segment",
        "no-bare-ok-in-traceback-build",
        "doeff-vm-no-dispatch-id-runtime",
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


def test_withhandler_return_clause_rule_detects_external_legacy_calls() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "doeff/withhandler_return_clause_sample.py",
        cwd=fixture_root,
    )

    assert _rule_start_lines(results, "doeff-withhandler-no-return-clause") == {20, 21, 22, 23}


def test_public_withhandler_rule_detects_legacy_hy_import_and_calls() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "doeff/public_withhandler_sample.hy",
        cwd=fixture_root,
    )

    assert _rule_start_lines(results, "doeff-no-public-withhandler-shim") == {1}


def test_agentd_only_worker_route_rule_detects_conductor_handler_bypass() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-conductor/src/doeff_conductor/handlers/agent_handler.py",
        cwd=fixture_root,
    )

    assert _has_rule(check_ids, "adr0001-d1-agentd-only-worker-route")


def test_k4_deadline_rule_bans_transport_timeout_on_agent_task_specs() -> None:
    """L-K4-3 guard: `timeout_seconds` must not return to AgentTask/AgentSpec."""
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-agents/src/doeff_agents/deadline_timeout_sample.py",
        cwd=fixture_root,
    )

    # Both the AgentTask and the AgentSpec construction must fire.
    assert len(_rule_start_lines(results, "k4-deadline-not-transport-timeout")) == 2


def test_real_agent_e2e_semgrep_rules_detect_missing_and_skipped_coverage() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-agents/tests",
        cwd=fixture_root,
    )

    expected = {
        "doeff-agents-require-real-claude-result-retry-e2e",
        "doeff-agents-require-real-codex-result-retry-e2e",
        "doeff-agents-real-agent-e2e-must-not-be-skipped",
        "doeff-agents-real-agent-e2e-must-not-use-command-override",
    }
    assert all(_has_rule(check_ids, rule_id) for rule_id in expected)


def test_agent_anthropic_api_key_semgrep_rule_detects_env_injection() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-agents/src",
        cwd=fixture_root,
    )

    assert _has_rule(check_ids, "doeff-agents-no-anthropic-api-key-agent-env")
