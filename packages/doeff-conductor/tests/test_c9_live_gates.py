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
from doeff_conductor.overseer import list_open_gates, progress_since
from doeff_conductor.replay_keying import ResolvedIdentity
from doeff_conductor.types import WorkflowStatus


def _cheap_session_id(run_id: str, node_id: str) -> str:
    return AgentTask(
        run_id=run_id,
        node_id=node_id,
        attempt=0,
        env=cast(Any, None),
        prompt="",
        result_schema={},
        verification_class="test-verifiable",
        agent_type="codex",
        resolved_identity=ResolvedIdentity(
            adapter="codex",
            model="",
            identity=None,
            effort="xhigh",
        ),
    ).session_id


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


def _write_parallel_gate_workflow(path: Path) -> None:
    path.write_text(
        """
(require doeff-hy.conductor [defworkflow agent! parallel <-])
(import doeff_conductor.dsl [artifact prompt ref])

(setv RESULT-SCHEMA {"type" "object"
                     "required" ["summary"]
                     "properties" {"summary" {"type" "string"}}
                     "additionalProperties" False})

(defworkflow live-gate
  :params {}
  :roles {"implementer" {"profile" "cheap-coder" "retry" 0}}
  (<- branches
      (parallel
        (agent! :role "implementer"
                :class "test-verifiable"
                :prompt (prompt "blocked branch")
                :schema RESULT-SCHEMA
                :label "blocked")
        (agent! :role "implementer"
                :class "test-verifiable"
                :prompt (prompt "independent branch")
                :schema RESULT-SCHEMA
                :label "independent")))
  (artifact (ref "branches")))

(setv WORKFLOW live-gate)
""".lstrip(),
        encoding="utf-8",
    )


def _write_checkpoint_workflow(path: Path) -> None:
    path.write_text(
        """
(require doeff-hy.conductor [defworkflow defphase <-])
(import doeff_conductor.dsl [artifact prompt ref])

(defworkflow checkpoint-live
  :params {}
  :roles {}
  (defphase Build
    :stakes "high"
    (<- built (prompt "built")))
  (artifact (ref "built")))

(setv WORKFLOW checkpoint-live)
""".lstrip(),
        encoding="utf-8",
    )


def test_live_retry_budget_exhaustion_opens_gate_and_parallel_sibling_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "live_gate.hy"
    state_dir = tmp_path / "state"
    run_id = "live-budget"
    _write_parallel_gate_workflow(workflow_path)

    runtime = MockConductorRuntime(tmp_path / "runtime")
    blocked_session_id = _cheap_session_id(run_id, "live-gate/0/parallel[0]/agent")
    independent_session_id = _cheap_session_id(run_id, "live-gate/0/parallel[1]/agent")
    runtime.configure_agent_script(blocked_session_id, [None])
    runtime.configure_agent_script(independent_session_id, [{"summary": "independent done"}])
    _install_mock_production_handlers(monkeypatch=monkeypatch, runtime=runtime)

    handle = ConductorAPI(state_dir=state_dir).run_workflow(
        str(workflow_path),
        run_id=run_id,
    )

    assert handle.status is WorkflowStatus.BLOCKED
    assert runtime.agent_invocation_count(blocked_session_id) == 1
    assert runtime.agent_invocation_count(independent_session_id) == 1

    gates = list_open_gates(state_dir, run_id)
    assert len(gates) == 1
    assert gates[0]["reason"] == "budget exhausted"
    assert gates[0]["stakes"]["verification_class"] == "test-verifiable"
    assert gates[0]["stakes"]["blast_radius"] == "dependent-subtree"
    assert {"proceed", "redirect", "abort"} <= {
        option["name"] for option in gates[0]["options"]
    }


def test_answer_proceed_records_journal_event_and_resumes_parked_subtree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "live_gate.hy"
    state_dir = tmp_path / "state"
    run_id = "answer-proceed"
    _write_parallel_gate_workflow(workflow_path)

    runtime = MockConductorRuntime(tmp_path / "runtime")
    blocked_session_id = _cheap_session_id(run_id, "live-gate/0/parallel[0]/agent")
    independent_session_id = _cheap_session_id(run_id, "live-gate/0/parallel[1]/agent")
    runtime.configure_agent_script(blocked_session_id, [None])
    runtime.configure_agent_script(independent_session_id, [{"summary": "independent done"}])
    _install_mock_production_handlers(monkeypatch=monkeypatch, runtime=runtime)

    first_handle = ConductorAPI(state_dir=state_dir).run_workflow(
        str(workflow_path),
        run_id=run_id,
    )
    assert first_handle.status is WorkflowStatus.BLOCKED
    gate_id = list_open_gates(state_dir, run_id)[0]["gate_id"]

    runtime.configure_agent_script(blocked_session_id, [{"summary": "blocked recovered"}])
    result = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(state_dir),
            "answer",
            run_id,
            gate_id,
            "proceed",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "done"
    assert list_open_gates(state_dir, run_id) == []
    assert runtime.agent_invocation_count(blocked_session_id) == 2
    assert runtime.agent_invocation_count(independent_session_id) == 1
    assert any(
        event["status"] == "answered" and event["message"].endswith("proceed")
        for event in progress_since(state_dir, run_id, 0)
    )


def test_live_phase_checkpoint_blocks_until_proceed(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "checkpoint_live.hy"
    state_dir = tmp_path / "state"
    run_id = "checkpoint-live"
    _write_checkpoint_workflow(workflow_path)
    api = ConductorAPI(state_dir=state_dir)

    first_handle = api.run_workflow(
        str(workflow_path),
        run_id=run_id,
        supervision="phase-checkpoints",
    )

    assert first_handle.status is WorkflowStatus.BLOCKED
    gates = list_open_gates(state_dir, run_id)
    assert len(gates) == 1
    assert gates[0]["reason"] == "phase checkpoint"
    assert gates[0]["stakes"]["binding_deltas"] == ["built"]
    assert "built" in gates[0]["stakes"]["artifact_summaries"]
    assert {"proceed", "redirect", "abort"} <= {
        option["name"] for option in gates[0]["options"]
    }

    result = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(state_dir),
            "answer",
            run_id,
            gates[0]["gate_id"],
            "proceed",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "done"
    assert payload["result_payload"] == "built"
    assert list_open_gates(state_dir, run_id) == []
