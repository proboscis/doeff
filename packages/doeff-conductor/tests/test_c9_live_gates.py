from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from click.testing import CliRunner
from doeff_conductor.api import ConductorAPI
from doeff_conductor.cli import cli
from doeff_conductor.effects import AgentEffect, AgentTask, RandomCall, TimeCall
from doeff_conductor.handlers.journaled_agent import JournaledAgentHandler
from doeff_conductor.handlers.testing import MockConductorRuntime, mock_handlers
from doeff_conductor.journal import GateAnswerJournal
from doeff_conductor.overseer import (
    RUN_STATE_FILENAME,
    VALID_GATE_OUTCOMES,
    list_gates_with_status,
    list_open_gates,
    progress_since,
)
from doeff_conductor.replay_keying import ResolvedIdentity
from doeff_conductor.types import WorkflowStatus
from doeff_conductor.workflow_effect_journal import (
    WORKFLOW_EFFECT_JOURNAL_FILENAME,
    JournaledWorkflowEffectHandler,
)


def _cheap_session_id(run_id: str, node_id: str, *, attempt: int = 0) -> str:
    return AgentTask(
        run_id=run_id,
        node_id=node_id,
        attempt=attempt,
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
        workflow_effect_handler = JournaledWorkflowEffectHandler(
            state_dir=cast(str | Path | None, kwargs["journal_state_dir"]),
            run_id=str(kwargs["journal_run_id"]),
        )
        return mock_handlers(
            runtime=runtime,
            overrides={
                AgentEffect: journaled_handler.handle_agent,
                TimeCall: workflow_effect_handler.handle_time,
                RandomCall: workflow_effect_handler.handle_random,
            },
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


def _write_random_parallel_gate_workflow(path: Path) -> None:
    path.write_text(
        """
(require doeff-hy.conductor [defworkflow agent! parallel random! <-])
(import doeff_conductor.dsl [artifact prompt ref])

(setv RESULT-SCHEMA {"type" "object"
                     "required" ["summary"]
                     "properties" {"summary" {"type" "string"}}
                     "additionalProperties" False})

(defworkflow random-live-gate
  :params {}
  :roles {"implementer" {"profile" "cheap-coder" "retry" 0}}
  (<- ticket (random!))
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
  (artifact {"ticket" (ref "ticket") "branches" (ref "branches")}))

(setv WORKFLOW random-live-gate)
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
    assert gates[0]["reason"] == "agent result validation failed"
    assert gates[0]["stakes"]["verification_class"] == "test-verifiable"
    assert gates[0]["stakes"]["blast_radius"] == "dependent-subtree"
    assert {"retry-agent", "redirect", "abort"} <= {
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
    blocked_retry_session_id = _cheap_session_id(
        run_id,
        "live-gate/0/parallel[0]/agent",
        attempt=1,
    )
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

    runtime.configure_agent_script(blocked_retry_session_id, [{"summary": "blocked recovered"}])
    result = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(state_dir),
            "answer",
            run_id,
            gate_id,
            "retry-agent",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "done"
    assert list_open_gates(state_dir, run_id) == []
    assert runtime.agent_invocation_count(blocked_session_id) == 1
    assert runtime.agent_invocation_count(blocked_retry_session_id) == 1
    assert runtime.agent_invocation_count(independent_session_id) == 1
    assert runtime.agent_prompts(blocked_session_id) == ["blocked branch"]
    retry_prompts = runtime.agent_prompts(blocked_retry_session_id)
    assert len(retry_prompts) == 1
    assert "blocked branch" in retry_prompts[0]
    assert "Previous structured result failure" in retry_prompts[0]
    assert "last_error_kind: absent" in retry_prompts[0]
    assert "last_error_message: result artifact is absent" in retry_prompts[0]
    assert any(
        event["status"] == "answered" and event["message"].endswith("retry-agent")
        for event in progress_since(state_dir, run_id, 0)
    )


def test_workflow_effect_journal_coexists_with_open_gate_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "random_live_gate.hy"
    state_dir = tmp_path / "state"
    run_id = "random-gate-coexist"
    _write_random_parallel_gate_workflow(workflow_path)

    runtime = MockConductorRuntime(tmp_path / "runtime")
    blocked_session_id = _cheap_session_id(run_id, "random-live-gate/1/parallel[0]/agent")
    blocked_retry_session_id = _cheap_session_id(
        run_id,
        "random-live-gate/1/parallel[0]/agent",
        attempt=1,
    )
    independent_session_id = _cheap_session_id(run_id, "random-live-gate/1/parallel[1]/agent")
    runtime.configure_agent_script(blocked_session_id, [None])
    runtime.configure_agent_script(independent_session_id, [{"summary": "independent done"}])
    _install_mock_production_handlers(monkeypatch=monkeypatch, runtime=runtime)

    first_handle = ConductorAPI(state_dir=state_dir).run_workflow(
        str(workflow_path),
        run_id=run_id,
    )

    assert first_handle.status is WorkflowStatus.BLOCKED
    run_dir = state_dir / "workflows" / run_id
    effect_journal_path = run_dir / WORKFLOW_EFFECT_JOURNAL_FILENAME
    run_state_path = run_dir / RUN_STATE_FILENAME
    assert effect_journal_path.exists()
    assert run_state_path.exists()
    effect_journal_text = effect_journal_path.read_text(encoding="utf-8")
    assert '"effect_kind":"random"' in effect_journal_text

    gates = list_open_gates(state_dir, run_id)
    assert len(gates) == 1
    run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
    assert run_state["open_gates"][0]["gate_id"] == gates[0]["gate_id"]

    runtime.configure_agent_script(blocked_retry_session_id, [{"summary": "blocked recovered"}])
    result = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(state_dir),
            "answer",
            run_id,
            gates[0]["gate_id"],
            "retry-agent",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "done"
    assert list_open_gates(state_dir, run_id) == []
    assert effect_journal_path.read_text(encoding="utf-8") == effect_journal_text


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


# =========================================================================
# K5 Gate Answer Journal Tests (L-K5-1 / L-K5-2)
# =========================================================================


def test_answer_abort_records_journal_and_terminates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L-K5-1: answer(abort) writes a journal entry and terminates the run."""
    workflow_path = tmp_path / "live_gate.hy"
    state_dir = tmp_path / "state"
    run_id = "abort-journal"
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

    result = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(state_dir),
            "answer",
            run_id,
            gate_id,
            "abort",
            "--note",
            "rejected by overseer",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "aborted"

    journal = GateAnswerJournal.for_run(run_id, state_dir=state_dir)
    entries = journal.load_entries()
    assert len(entries) == 1
    assert entries[0].gate_id == gate_id
    assert entries[0].option == "abort"
    assert entries[0].outcome == "abort"
    assert entries[0].note == "rejected by overseer"


def test_answer_redirect_records_journal_and_stays_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L-K5-1: redirect records journal, stays BLOCKED with snapshot guidance."""
    workflow_path = tmp_path / "live_gate.hy"
    state_dir = tmp_path / "state"
    run_id = "redirect-journal"
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

    result = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(state_dir),
            "answer",
            run_id,
            gate_id,
            "redirect",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert "conductor resume" in payload["error"]
    assert "workflow.hy" in payload["error"]

    journal = GateAnswerJournal.for_run(run_id, state_dir=state_dir)
    entries = journal.load_entries()
    assert len(entries) == 1
    assert entries[0].option == "redirect"
    assert entries[0].outcome == "resume"

    runtime.configure_agent_script(blocked_session_id, [{"summary": "blocked recovered"}])
    resumed_handle = ConductorAPI(state_dir=state_dir).resume_workflow(run_id)
    assert resumed_handle.status is WorkflowStatus.DONE


def test_answer_replay_determinism_l_k5_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L-K5-2: answer is part of replay identity; re-resume replays the same decision."""
    workflow_path = tmp_path / "live_gate.hy"
    state_dir = tmp_path / "state"
    run_id = "replay-determinism"
    _write_parallel_gate_workflow(workflow_path)

    runtime = MockConductorRuntime(tmp_path / "runtime")
    blocked_session_id = _cheap_session_id(run_id, "live-gate/0/parallel[0]/agent")
    blocked_retry_session_id = _cheap_session_id(
        run_id,
        "live-gate/0/parallel[0]/agent",
        attempt=1,
    )
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

    runtime.configure_agent_script(blocked_retry_session_id, [{"summary": "blocked recovered"}])
    api = ConductorAPI(state_dir=state_dir)
    handle_1 = api.answer_gate(run_id, gate_id, "retry-agent")
    assert handle_1.status is WorkflowStatus.DONE

    journal = GateAnswerJournal.for_run(run_id, state_dir=state_dir)
    answers_after_first = journal.latest_answers()
    assert answers_after_first[gate_id] == "retry-agent"

    handle_2 = api.resume_workflow(run_id)
    assert handle_2.status is WorkflowStatus.DONE

    answers_after_second = journal.latest_answers()
    assert answers_after_second[gate_id] == "retry-agent"


def test_gate_list_shows_answered_gates_as_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate list shows answered gates with status=answered."""
    workflow_path = tmp_path / "live_gate.hy"
    state_dir = tmp_path / "state"
    run_id = "gate-list-status"
    _write_parallel_gate_workflow(workflow_path)

    runtime = MockConductorRuntime(tmp_path / "runtime")
    blocked_session_id = _cheap_session_id(run_id, "live-gate/0/parallel[0]/agent")
    blocked_retry_session_id = _cheap_session_id(
        run_id,
        "live-gate/0/parallel[0]/agent",
        attempt=1,
    )
    independent_session_id = _cheap_session_id(run_id, "live-gate/0/parallel[1]/agent")
    runtime.configure_agent_script(blocked_session_id, [None])
    runtime.configure_agent_script(independent_session_id, [{"summary": "independent done"}])
    _install_mock_production_handlers(monkeypatch=monkeypatch, runtime=runtime)

    ConductorAPI(state_dir=state_dir).run_workflow(
        str(workflow_path),
        run_id=run_id,
    )
    gates_before = list_gates_with_status(state_dir, run_id)
    assert len(gates_before) == 1
    assert gates_before[0]["status"] == "open"

    gate_id = gates_before[0]["gate_id"]
    runtime.configure_agent_script(blocked_retry_session_id, [{"summary": "blocked recovered"}])
    ConductorAPI(state_dir=state_dir).answer_gate(run_id, gate_id, "retry-agent")

    gates_after = list_gates_with_status(state_dir, run_id)
    answered_gates = [g for g in gates_after if g["status"] == "answered"]
    assert len(answered_gates) == 1
    assert answered_gates[0]["gate_id"] == gate_id
    assert answered_gates[0]["option"] == "retry-agent"


def test_gate_answer_cli_subcommand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """conductor gate answer renders and works."""
    workflow_path = tmp_path / "live_gate.hy"
    state_dir = tmp_path / "state"
    run_id = "gate-answer-cli"
    _write_parallel_gate_workflow(workflow_path)

    runtime = MockConductorRuntime(tmp_path / "runtime")
    blocked_session_id = _cheap_session_id(run_id, "live-gate/0/parallel[0]/agent")
    blocked_retry_session_id = _cheap_session_id(
        run_id,
        "live-gate/0/parallel[0]/agent",
        attempt=1,
    )
    independent_session_id = _cheap_session_id(run_id, "live-gate/0/parallel[1]/agent")
    runtime.configure_agent_script(blocked_session_id, [None])
    runtime.configure_agent_script(independent_session_id, [{"summary": "independent done"}])
    _install_mock_production_handlers(monkeypatch=monkeypatch, runtime=runtime)

    ConductorAPI(state_dir=state_dir).run_workflow(
        str(workflow_path),
        run_id=run_id,
    )
    gate_id = list_open_gates(state_dir, run_id)[0]["gate_id"]

    runtime.configure_agent_script(blocked_retry_session_id, [{"summary": "blocked recovered"}])
    result = CliRunner().invoke(
        cli,
        [
            "--state-dir",
            str(state_dir),
            "gate",
            "answer",
            run_id,
            gate_id,
            "retry-agent",
            "--note",
            "approved via gate subcommand",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "done"

    journal = GateAnswerJournal.for_run(run_id, state_dir=state_dir)
    entries = journal.load_entries()
    assert len(entries) == 1
    assert entries[0].note == "approved via gate subcommand"


def test_gate_option_outcome_validation() -> None:
    """Every GateOption constructed via from_dict must have outcome in VALID_GATE_OUTCOMES."""
    from doeff_conductor.overseer import GateOption

    valid_data: dict[str, str] = {
        "name": "proceed",
        "outcome": "resume",
        "description": "Continue",
    }
    gate_option: GateOption = GateOption.from_dict(valid_data)
    assert gate_option.outcome in VALID_GATE_OUTCOMES

    invalid_data: dict[str, str] = {
        "name": "proceed",
        "outcome": "invalid_outcome",
        "description": "Continue",
    }
    with pytest.raises(ValueError, match="not in"):
        GateOption.from_dict(invalid_data)
