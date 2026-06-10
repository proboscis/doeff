from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from doeff_conductor.verbs import plan_workflow, validate_workflow
from doeff_conductor.workflow_loader import load_workflow_spec


def _workflow_path() -> Path:
    return (
        Path(__file__).parents[1]
        / "examples"
        / "k2_k3_pilot_workflow.hy"
    )


def _setup_script_path() -> Path:
    return (
        Path(__file__).parents[1]
        / "examples"
        / "setup_k2_k3_scratch_repo.py"
    )


def test_c7_pilot_workflow_plans_reference_worker_shape() -> None:
    workflow = load_workflow_spec(str(_workflow_path()))

    plan = plan_workflow(workflow)

    assert plan.workflow_name == "k2_k3_pilot"
    assert len(plan.rows) == 13
    assert plan.totals_by_profile == {"cheap-coder": 9, "cheap-reviewer": 4}
    assert [row.label for row in plan.rows[:5]] == [
        "impl:A-failure-kind",
        "impl:B-router",
        "impl:C-gates",
        "impl:D-investigation",
        "impl:E-ownership",
    ]
    assert [row.label for row in plan.rows[-4:]] == [
        "review-routing-closure",
        "review-tests",
        "review-ownership",
        "review-known-blocker",
    ]


def test_c7_pilot_workflow_validates_closure_scenarios() -> None:
    workflow = load_workflow_spec(str(_workflow_path()))

    report = validate_workflow(workflow)

    assert report.to_dict()["closure_ok"]
    assert {scenario.scenario for scenario in report.scenarios} == {
        "all-pass",
        "schema-invalid-then-pass",
        "retry-exhaustion",
        "quorum-shortfall",
    }


def test_c8_pilot_workflow_is_dsl_only() -> None:
    source = _workflow_path().read_text(encoding="utf-8")

    assert "(defn main" not in source
    assert "report_path" not in source


def test_c7_scratch_repo_has_build_test_lint_gate_shape(tmp_path: Path) -> None:
    repo_path = tmp_path / "scratch"
    subprocess.run(
        [sys.executable, str(_setup_script_path()), str(repo_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    build = subprocess.run(
        [sys.executable, "tools/build_check.py"],
        cwd=repo_path,
        check=False,
        capture_output=True,
        text=True,
    )
    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=repo_path,
        env={**os.environ, "PYTHONPATH": "src"},
        check=False,
        capture_output=True,
        text=True,
    )
    lint = subprocess.run(
        [sys.executable, "tools/lint_check.py"],
        cwd=repo_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert build.returncode == 0
    assert tests.returncode == 0
    assert lint.returncode == 1
    assert "C7_BLOCKER_LINT_SENTINEL" in (repo_path / "src/pilot_pkg/lint_sentinel.py").read_text(
        encoding="utf-8"
    )
    assert "lint sentinel remains" in lint.stdout
