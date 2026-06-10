from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from click.testing import CliRunner
from doeff_agents.effects import deterministic_session_id
from doeff_agents.effects.agent import AgentAttemptExhaustedError
from doeff_conductor.api import ConductorAPI
from doeff_conductor.cli import cli
from doeff_conductor.effects import AgentEffect
from doeff_conductor.handlers.journaled_agent import JournaledAgentHandler
from doeff_conductor.handlers.testing import MockConductorRuntime, mock_handlers
from doeff_conductor.types import WorkflowStatus
from doeff_conductor.workflow_loader import (
    WorkflowNondeterminismError,
    load_workflow_spec,
)

FIXTURES = Path(__file__).parent / "fixtures" / "workflow_nondeterminism"


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


def test_loader_rejects_python_workflow_surface(tmp_path: Path) -> None:
    workflow_path = tmp_path / "user_workflow.py"
    workflow_path.write_text("WORKFLOW = object()\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"\.hy.*Hy macro DSL"):
        load_workflow_spec(str(workflow_path))


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
    first_session_id = deterministic_session_id(
        run_id=run_id,
        node_id="single-file/1/agent",
        attempt=0,
    )
    second_session_id = deterministic_session_id(
        run_id=run_id,
        node_id="single-file/2/agent",
        attempt=0,
    )
    runtime.configure_agent_script(first_session_id, [{"summary": "first"}])
    runtime.configure_agent_script(second_session_id, [None])
    _install_mock_production_handlers(monkeypatch=monkeypatch, runtime=runtime)

    api = ConductorAPI(state_dir=state_dir)
    with pytest.raises(AgentAttemptExhaustedError):
        api.run_workflow(str(workflow_path), run_id=run_id)

    failed_handle = api.get_workflow(run_id)
    assert failed_handle is not None
    assert failed_handle.status == WorkflowStatus.ERROR
    assert (state_dir / "workflows" / run_id / "workflow.hy").exists()

    workflow_path.unlink()
    runtime.configure_agent_script(second_session_id, [{"summary": "second"}])
    resumed_handle = api.resume_workflow(run_id)

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
    first_session_id = deterministic_session_id(
        run_id=run_id,
        node_id="single-file/1/agent",
        attempt=0,
    )
    second_session_id = deterministic_session_id(
        run_id=run_id,
        node_id="single-file/2/agent",
        attempt=0,
    )
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
