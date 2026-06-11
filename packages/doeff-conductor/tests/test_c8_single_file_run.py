from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from click.testing import CliRunner
from doeff_conductor.api import ConductorAPI
from doeff_conductor.cli import cli
from doeff_conductor.effects import AgentEffect, AgentTask
from doeff_conductor.handlers.journaled_agent import JournaledAgentHandler
from doeff_conductor.handlers.testing import MockConductorRuntime, mock_handlers
from doeff_conductor.overseer import list_open_gates
from doeff_conductor.replay_keying import ResolvedIdentity
from doeff_conductor.types import WorkflowStatus
from doeff_conductor.workflow_loader import (
    WorkflowNondeterminismError,
    load_workflow_spec,
    snapshot_workflow_source,
)

FIXTURES = Path(__file__).parent / "fixtures" / "workflow_nondeterminism"



def _cheap_session_id(run_id: str, node_id: str) -> str:
    """Session id as the runtime derives it for the default cheap-coder profile."""
    return AgentTask(
        run_id=run_id,
        node_id=node_id,
        attempt=0,
        env=cast(Any, None),
        prompt="",
        result_schema={},
        verification_class="mechanical",
        agent_type="codex",
        resolved_identity=ResolvedIdentity(
            adapter="codex", model="", identity=None, effort="xhigh"
        ),
    ).session_id

def _write_single_file_workflow(path: Path) -> None:
    path.write_text(
        """
(require doeff-hy.conductor [defworkflow agent! workspace! <-])
(import doeff_conductor.dsl [artifact prompt ref])

(setv RESULT-SCHEMA {"type" "object"
                     "required" ["summary"]
                     "properties" {"summary" {"type" "string"}}
                     "additionalProperties" False})

(defworkflow single-file
  :params {}
  :roles {"implementer" {"profile" "cheap-coder" "retry" 0}}
  (<- workspace (workspace! :from "main"))
  (<- first
      (agent! :role "implementer"
              :class "test-verifiable"
              :prompt (prompt "first")
              :schema RESULT-SCHEMA
              :workspace (ref "workspace")
              :label "first"))
  (<- second
      (agent! :role "implementer"
              :class "test-verifiable"
              :prompt (prompt "second after " (ref "first"))
              :schema RESULT-SCHEMA
              :workspace (ref "workspace")
              :label "second"))
  (artifact (ref "second")))

(setv WORKFLOW single-file)
""".lstrip(),
        encoding="utf-8",
    )


def _install_mock_production_handlers(
    *,
    monkeypatch: pytest.MonkeyPatch,
    runtime: MockConductorRuntime,
) -> None:
    import doeff_conductor.handlers as handlers_module

    def production_handlers(**kwargs: object):
        journaled_handler = JournaledAgentHandler(
            runtime.handle_agent,
            state_dir=cast(str | Path | None, kwargs["journal_state_dir"]),
            run_id=str(kwargs["journal_run_id"]),
        )
        return mock_handlers(
            runtime=runtime,
            overrides={AgentEffect: journaled_handler.handle_agent},
        )

    monkeypatch.setattr(handlers_module, "production_handlers", production_handlers)


@pytest.mark.parametrize(
    ("fixture_name", "replacement"),
    [
        ("datetime_now.hy", "time!"),
        ("datetime_today.hy", "time!"),
        ("time_time.hy", "time!"),
        ("time_monotonic.hy", "time!"),
        ("random_call.hy", "random!"),
        ("open_call.hy", "gate!"),
        ("pathlib_write.hy", "gate!"),
        ("subprocess_call.hy", "gate!"),
        ("requests_import.hy", "gate!"),
        ("httpx_import.hy", "gate!"),
        ("socket_import.hy", "gate!"),
        ("urllib_import.hy", "gate!"),
        ("non_allowlisted_import.hy", ":params"),
        ("file_noqa_workflow.hy", "random!"),
        ("plain_module_with_random.hy", "random!"),
    ],
)
def test_loader_nondeterminism_fixtures_fail_loudly(
    fixture_name: str,
    replacement: str,
) -> None:
    with pytest.raises(WorkflowNondeterminismError) as error:
        load_workflow_spec(str(FIXTURES / fixture_name))

    assert replacement in str(error.value)


def test_clean_workflow_fixture_loads() -> None:
    workflow = load_workflow_spec(str(FIXTURES / "clean_workflow.hy"))

    assert workflow.name == "clean-workflow"


def test_loader_rejects_user_python_workflow(tmp_path: Path) -> None:
    """Tier-0 guard: user-authored .py workflows are rejected, naming the Hy surface."""
    workflow_path = tmp_path / "workflow.py"
    workflow_path.write_text("WORKFLOW = None\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"Hy macro DSL") as error:
        load_workflow_spec(str(workflow_path))

    message = str(error.value)
    assert ".hy" in message
    assert "doeff_hy.conductor" in message


def test_snapshot_rejects_user_python_workflow(tmp_path: Path) -> None:
    workflow_path = tmp_path / "workflow.py"
    workflow_path.write_text("WORKFLOW = None\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"Hy macro DSL"):
        snapshot_workflow_source(
            str(workflow_path),
            state_dir=tmp_path / "state",
            run_id="rejected",
        )


def test_cli_plan_rejects_user_python_workflow(tmp_path: Path) -> None:
    workflow_path = tmp_path / "workflow.py"
    workflow_path.write_text("WORKFLOW = None\n", encoding="utf-8")

    result = CliRunner().invoke(cli, ["plan", str(workflow_path), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert ".hy" in payload["error"]
    assert "Hy macro DSL" in payload["error"]


def test_cli_validate_accepts_hy_workflow(tmp_path: Path) -> None:
    workflow_path = tmp_path / "ephemeral_workflow.hy"
    _write_single_file_workflow(workflow_path)

    result = CliRunner().invoke(cli, ["validate", str(workflow_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["closure_ok"]


def test_plan_snapshots_ephemeral_workflow_source(tmp_path: Path) -> None:
    workflow_path = tmp_path / "ephemeral_workflow.hy"
    state_dir = tmp_path / "state"
    _write_single_file_workflow(workflow_path)

    result = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(state_dir),
            "plan",
            str(workflow_path),
            "--run-id",
            "plan-c8",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (state_dir / "workflows" / "plan-c8" / "workflow.hy").read_text(
        encoding="utf-8"
    ) == workflow_path.read_text(encoding="utf-8")


def test_single_file_dsl_run_returns_payload_and_resumes_from_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "ephemeral_workflow.hy"
    state_dir = tmp_path / "state"
    run_id = "c8-resume"
    _write_single_file_workflow(workflow_path)

    runtime = MockConductorRuntime(tmp_path / "runtime")
    first_session_id = _cheap_session_id(run_id, "single-file/1/agent")
    second_session_id = _cheap_session_id(run_id, "single-file/2/agent")
    runtime.configure_agent_script(first_session_id, [{"summary": "first"}])
    runtime.configure_agent_script(second_session_id, [None])
    _install_mock_production_handlers(monkeypatch=monkeypatch, runtime=runtime)

    api = ConductorAPI(state_dir=state_dir)
    blocked_handle = api.run_workflow(str(workflow_path), run_id=run_id)

    assert blocked_handle.status == WorkflowStatus.BLOCKED
    assert list_open_gates(state_dir, run_id)
    assert (state_dir / "workflows" / run_id / "workflow.hy").exists()

    workflow_path.unlink()
    runtime.configure_agent_script(second_session_id, [{"summary": "second"}])
    gate_id = list_open_gates(state_dir, run_id)[0]["gate_id"]
    resumed_handle = api.answer_gate(run_id, gate_id, "proceed")

    assert resumed_handle.status == WorkflowStatus.DONE
    assert resumed_handle.result_payload == {"summary": "second"}
    assert runtime.agent_invocation_count(first_session_id) == 1
    assert runtime.agent_invocation_count(second_session_id) == 2


def test_cli_run_json_includes_workflow_return_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "ephemeral_workflow.hy"
    state_dir = tmp_path / "state"
    run_id = "c8-json"
    _write_single_file_workflow(workflow_path)

    runtime = MockConductorRuntime(tmp_path / "runtime")
    first_session_id = _cheap_session_id(run_id, "single-file/1/agent")
    second_session_id = _cheap_session_id(run_id, "single-file/2/agent")
    runtime.configure_agent_script(first_session_id, [{"summary": "first"}])
    runtime.configure_agent_script(second_session_id, [{"summary": "json-result"}])
    _install_mock_production_handlers(monkeypatch=monkeypatch, runtime=runtime)

    result = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(state_dir),
            "run",
            str(workflow_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "done"
    assert payload["result_payload"] == {"summary": "json-result"}
