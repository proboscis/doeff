from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from doeff_conductor.cli import cli
from doeff_conductor.dsl import agent_bang, defworkflow
from doeff_conductor.environment import ProfileRegistry, load_profile_registry_from_env
from doeff_conductor.overseer import list_open_gates, progress_since
from doeff_conductor.verbs import (
    BUILT_IN_VALIDATION_SCENARIOS,
    ScenarioValidationReport,
    TerminalState,
    assert_validation_closure,
    plan_workflow,
    validate_workflow,
)
from doeff_conductor.workflow_loader import load_workflow_spec

RESULT_SCHEMA = {
    "type": "object",
    "required": ["status"],
    "properties": {"status": {"type": "string"}},
}


def _fixture_path() -> Path:
    return Path(__file__).parent / "fixtures" / "c6_k2_k3_workflow.hy"


def _cascade_agent(role: str, verification_class: str, **overrides: object) -> object:
    fields = {
        "role": role,
        "verification_class": verification_class,
        "prompt": f"{role} prompt",
        "schema": RESULT_SCHEMA,
        "label": role,
    }
    fields.update(overrides)
    return agent_bang(**fields)


def test_plan_resolves_k2_k3_fixture_without_executing_agents() -> None:
    workflow = load_workflow_spec(str(_fixture_path()))

    plan = plan_workflow(workflow)

    assert plan.interpreter == "plan"
    assert len(plan.rows) == 13
    assert plan.estimated_budget_units == 13
    assert plan.capabilities_satisfied
    assert plan.totals_by_profile == {"cheap-coder": 9, "cheap-reviewer": 4}
    assert {row.phase for row in plan.rows} == {
        "Implement",
        "Fix",
        "Tests",
        "Gate",
        "Review",
    }
    assert all(row.fingerprint for row in plan.rows)


def test_plan_profile_resolution_uses_single_cascade() -> None:
    workflow = defworkflow(
        "cascade",
        params={},
        roles={
            "explicit": {},
            "role": {"profile": "cheap-reviewer"},
            "router": {},
            "env": {},
        },
        body=[
            _cascade_agent("explicit", "semantic", profile="frontier-author"),
            _cascade_agent("role", "semantic"),
            _cascade_agent("router", "test-verifiable"),
            _cascade_agent("env", "novel"),
        ],
    )

    plan = plan_workflow(workflow)

    assert [row.profile for row in plan.rows] == [
        "frontier-author",
        "cheap-reviewer",
        "cheap-coder",
        "cheap-coder",
    ]
    assert [row.resolution_source for row in plan.rows] == [
        "explicit",
        "role",
        "router",
        "interpreter-env",
    ]


def test_plan_fails_when_profile_does_not_resolve() -> None:
    workflow = defworkflow(
        "missing_profile",
        params={},
        roles={"implementer": {"profile": "missing-profile"}},
        body=[_cascade_agent("implementer", "test-verifiable")],
    )

    with pytest.raises(ValueError, match="profile"):
        plan_workflow(workflow)


def test_env_describe_uses_semantic_profile_surface_only() -> None:
    registry: ProfileRegistry = load_profile_registry_from_env()
    description = registry.to_public_dict()

    assert {profile["name"] for profile in description["profiles"]} >= {
        "cheap-coder",
        "cheap-reviewer",
    }
    assert all("adapter" not in profile for profile in description["profiles"])
    assert all("model" not in profile for profile in description["profiles"])


def test_validate_runs_built_in_scenarios_and_asserts_closure() -> None:
    workflow = load_workflow_spec(str(_fixture_path()))

    report = validate_workflow(workflow)

    assert report.interpreter == "validation"
    assert [scenario.scenario for scenario in report.scenarios] == list(
        BUILT_IN_VALIDATION_SCENARIOS
    )
    assert all(scenario.to_dict()["closure_ok"] for scenario in report.scenarios)
    assert any(
        gate.reason == "agent retry exhaustion"
        for scenario in report.scenarios
        for gate in scenario.open_gates
    )
    assert any(
        terminal.detail == "schema invalid retry then pass"
        for scenario in report.scenarios
        for terminal in scenario.terminals
    )


def test_validate_closure_assertion_fails_loudly_for_broken_terminal() -> None:
    broken = ScenarioValidationReport(
        scenario="broken",
        workflow_name="broken",
        terminals=(
            TerminalState(
                node_id="broken/node",
                terminal_kind="silent-drop",
                status="done",
            ),
        ),
        open_gates=(),
    )

    with pytest.raises(ValueError, match="closure law violation"):
        assert_validation_closure(broken)


def test_validate_materializes_gate_queue_and_progress_deltas(tmp_path: Path) -> None:
    workflow = load_workflow_spec(str(_fixture_path()))

    validate_workflow(
        workflow,
        scenarios=("all-pass",),
        supervision="phase-checkpoints",
        state_dir=str(tmp_path),
        run_id="run-c6",
    )

    gates = list_open_gates(tmp_path, "run-c6")
    deltas = progress_since(tmp_path, "run-c6", 0)

    assert {gate["reason"] for gate in gates} == {"phase checkpoint"}
    assert {"proceed", "abort"} <= {
        option["name"]
        for gate in gates
        for option in gate["options"]
    }
    assert deltas
    assert deltas[0]["sequence"] == 1


def test_cli_plan_validate_env_gate_and_show_since(tmp_path: Path) -> None:
    runner = CliRunner()
    fixture_path = str(_fixture_path())

    plan_result = runner.invoke(cli, ["plan", fixture_path, "--json"])
    assert plan_result.exit_code == 0
    plan_payload = json.loads(plan_result.output)
    assert plan_payload["workflow_name"] == "k2_k3_reference_shape"
    assert len(plan_payload["rows"]) == 13

    env_result = runner.invoke(cli, ["env", "describe", "--json"])
    assert env_result.exit_code == 0
    env_payload = json.loads(env_result.output)
    assert "router_default_policy" in env_payload
    assert all("adapter" not in profile for profile in env_payload["profiles"])

    validate_result = runner.invoke(
        cli,
        [
            "--state-dir",
            str(tmp_path),
            "validate",
            fixture_path,
            "--scenario",
            "all-pass",
            "--supervision",
            "phase-checkpoints",
            "--run-id",
            "run-c6",
            "--json",
        ],
    )
    assert validate_result.exit_code == 0
    validate_payload = json.loads(validate_result.output)
    assert validate_payload["closure_ok"]

    gate_result = runner.invoke(
        cli,
        ["--state-dir", str(tmp_path), "gate", "list", "run-c6", "--json"],
    )
    assert gate_result.exit_code == 0
    assert json.loads(gate_result.output)

    show_result = runner.invoke(
        cli,
        ["--state-dir", str(tmp_path), "show", "run-c6", "--since", "0", "--json"],
    )
    assert show_result.exit_code == 0
    assert json.loads(show_result.output)[0]["sequence"] == 1
