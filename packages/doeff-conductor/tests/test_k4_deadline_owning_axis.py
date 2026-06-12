"""K4-edge x K5 tests for the await-deadline owning axis (L-K4-3).

Issue: conductor-await-deadline-owning-axis (validation ledger §11-7).

The wall-clock deadline is an ``agent!`` node-spec attribute
(``:deadline-seconds``), observed by the real L2 attempt loop
(``_run_agent_task``) and parked by the L3 runtime as a K5 gate on
exceed. Extension is ONLY a gate answer (``extend``), journaled in
``gate-answer-journal.jsonl`` and replayed deterministically (L-K5-2).
Transport heartbeat expiry (``AwaitStatus.TIMED_OUT``) is transparent —
never a node failure, never an attempt burn.

These tests run the REAL workflow runtime (``ConductorAPI.run_workflow``
over a Hy DSL file) and the REAL L2 attempt loop; only the transport
seam is scripted (``ScenarioAgentHandler`` await outcomes), mirroring
the C9 live-gate suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from doeff_agents import (
    AgentEffect as AgentsAgentEffect,
)
from doeff_agents import (
    AgentTask as AgentsAgentTask,
)
from doeff_agents import AgentType
from doeff_agents.handlers.testing import ScenarioAgentHandler, ScenarioStep
from doeff_conductor.api import ConductorAPI
from doeff_conductor.effects import AgentEffect, AgentTask
from doeff_conductor.handlers.agent_handler import AgentHandler
from doeff_conductor.handlers.journaled_agent import JournaledAgentHandler
from doeff_conductor.handlers.testing import MockConductorRuntime, mock_handlers
from doeff_conductor.journal import (
    AgentJournal,
    GateAnswerJournal,
)
from doeff_conductor.overseer import list_open_gates
from doeff_conductor.replay_keying import ResolvedIdentity
from doeff_conductor.types import WorkflowStatus

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
    "additionalProperties": False,
}


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


class _ScenarioConductorBackend:
    """Conductor AgentBackend driving the REAL L2 attempt loop.

    Mirrors ``AgentdAgentBackend.handle_agent`` exactly (conductor task →
    agents task, deadline included) but binds the L2 algebra to
    ``ScenarioAgentHandler`` instead of the agentd daemon, so the
    deadline/heartbeat enforcement under test is the production
    ``_run_agent_task`` code path with scripted transport outcomes.
    """

    def __init__(self, scenario_handler: ScenarioAgentHandler) -> None:
        self.scenario_handler = scenario_handler

    def handle_agent(self, effect: AgentEffect, workspace_resolver: Any) -> object:
        return self.scenario_handler.handle_agent(
            AgentsAgentEffect(
                task=AgentsAgentTask(
                    run_id=effect.task.run_id,
                    node_id=effect.task.session_node_key,
                    attempt=effect.task.attempt,
                    agent_type=AgentType(effect.task.agent_type),
                    work_dir=workspace_resolver(effect.task.env),
                    prompt=effect.task.prompt,
                    result_schema=effect.task.result_schema,
                    model=effect.task.model,
                    effort=effect.task.effort,
                    max_retries=effect.task.max_retries,
                    deadline_seconds=effect.task.deadline_seconds,
                )
            )
        )


def _install_scenario_production_handlers(
    *,
    monkeypatch: pytest.MonkeyPatch,
    runtime: MockConductorRuntime,
    scenario_handler: ScenarioAgentHandler,
    delegate_calls: list[str],
) -> None:
    import doeff_conductor.handlers as handlers_module

    agent_handler = AgentHandler(
        workspace_resolver=runtime.resolve_path,
        backend=_ScenarioConductorBackend(scenario_handler),
    )

    def counting_delegate(effect: AgentEffect) -> object:
        delegate_calls.append(effect.task.node_id)
        return agent_handler.handle_agent(effect)

    def production_handlers(**kwargs: object):
        journaled_handler = JournaledAgentHandler(
            counting_delegate,
            state_dir=cast(str | Path | None, kwargs["journal_state_dir"]),
            run_id=str(kwargs["journal_run_id"]),
        )
        return mock_handlers(
            runtime=runtime,
            overrides={AgentEffect: journaled_handler.handle_agent},
        )

    monkeypatch.setattr(handlers_module, "production_handlers", production_handlers)


def _write_deadline_workflow(path: Path, *, deadline_seconds: float) -> None:
    path.write_text(
        f"""
(require doeff-hy.conductor [defworkflow agent! <-])
(import doeff_conductor.dsl [artifact prompt ref])

(setv RESULT-SCHEMA {{"type" "object"
                     "required" ["summary"]
                     "properties" {{"summary" {{"type" "string"}}}}
                     "additionalProperties" False}})

(defworkflow deadline-live
  :params {{}}
  :roles {{"implementer" {{"profile" "cheap-coder" "retry" 0}}}}
  (<- result
      (agent! :role "implementer"
              :class "test-verifiable"
              :prompt (prompt "slow work")
              :schema RESULT-SCHEMA
              :deadline-seconds {deadline_seconds}
              :label "slow"))
  (artifact (ref "result")))

(setv WORKFLOW deadline-live)
""".lstrip(),
        encoding="utf-8",
    )


def _park_run_on_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_id: str,
) -> tuple[ConductorAPI, Path, str, ScenarioAgentHandler, list[str]]:
    """Run a short-deadline workflow against a never-completing stub agent."""
    workflow_path = tmp_path / "deadline_live.hy"
    state_dir = tmp_path / "state"
    _write_deadline_workflow(workflow_path, deadline_seconds=0.2)

    session_id = _cheap_session_id(run_id, "deadline-live/0/agent")
    # The stub never completes: every transport await expires (TIMED_OUT)
    # until the node-spec deadline is exceeded.
    scenario_handler = ScenarioAgentHandler(
        scripts={session_id: [ScenarioStep.timeout()]},
    )
    runtime = MockConductorRuntime(tmp_path / "runtime")
    delegate_calls: list[str] = []
    _install_scenario_production_handlers(
        monkeypatch=monkeypatch,
        runtime=runtime,
        scenario_handler=scenario_handler,
        delegate_calls=delegate_calls,
    )

    api = ConductorAPI(state_dir=state_dir)
    handle = api.run_workflow(str(workflow_path), run_id=run_id)
    assert handle.status is WorkflowStatus.BLOCKED
    return api, state_dir, session_id, scenario_handler, delegate_calls


def test_v1_deadline_exceeded_parks_k5_gate_with_journal_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V1: short deadline + never-completing stub agent → K5 gate park.

    The gate reason names the wall-clock deadline and the park is a
    journaled open-gate terminal in agent-journal.jsonl.
    """
    run_id = "deadline-park"
    _, state_dir, _session_id, _scenario, delegate_calls = _park_run_on_deadline(
        tmp_path, monkeypatch, run_id
    )

    gates = list_open_gates(state_dir, run_id)
    assert len(gates) == 1
    assert gates[0]["reason"] == "wall-clock deadline exceeded"
    assert gates[0]["gate_id"].endswith(":deadline-exceeded")
    assert gates[0]["stakes"]["deadline_seconds"] == 0.2
    assert gates[0]["stakes"]["elapsed_seconds"] >= 0.2
    assert gates[0]["stakes"]["verification_class"] == "test-verifiable"
    assert gates[0]["stakes"]["blast_radius"] == "dependent-subtree"
    assert {"extend", "redirect", "abort"} == {
        option["name"] for option in gates[0]["options"]
    }
    assert delegate_calls == ["deadline-live/0/agent"]

    journal_entries = AgentJournal.for_run(run_id, state_dir=state_dir).load_entries()
    assert len(journal_entries) == 1
    assert journal_entries[0].terminal_kind == "open-gate"
    assert journal_entries[0].result_artifact["reason"] == "wall-clock deadline exceeded"
    assert journal_entries[0].result_artifact["deadline_seconds"] == 0.2


def test_v2_extend_answer_reawaits_completes_and_replays_without_reparking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V2: extend → re-await completes; answer journaled; replay is decision-stable.

    The extend answer is appended to gate-answer-journal.jsonl, the resumed
    run re-awaits the node with a fresh deadline window and finishes, and
    re-running the FINISHED run id replays the journaled artifact without
    invoking the agent again or re-parking.
    """
    run_id = "deadline-extend"
    api, state_dir, session_id, scenario_handler, delegate_calls = _park_run_on_deadline(
        tmp_path, monkeypatch, run_id
    )
    gate_id = list_open_gates(state_dir, run_id)[0]["gate_id"]

    scenario_handler.configure_script(
        session_id, [ScenarioStep.success({"summary": "finished in extension"})]
    )
    extended_handle = api.answer_gate(run_id, gate_id, "extend", note="renewal granted")
    assert extended_handle.status is WorkflowStatus.DONE
    assert extended_handle.result_payload == {"summary": "finished in extension"}
    assert list_open_gates(state_dir, run_id) == []
    # First run parked, extension re-awaited: exactly two delegate executions.
    assert len(delegate_calls) == 2

    answer_entries = GateAnswerJournal.for_run(run_id, state_dir=state_dir).load_entries()
    assert len(answer_entries) == 1
    assert answer_entries[0].gate_id == gate_id
    assert answer_entries[0].option == "extend"
    assert answer_entries[0].outcome == "resume"
    assert answer_entries[0].note == "renewal granted"

    # L-K5-2: re-running the finished run id replays the same decision —
    # journal hit, no agent re-execution, no re-park.
    replayed_handle = api.resume_workflow(run_id)
    assert replayed_handle.status is WorkflowStatus.DONE
    assert replayed_handle.result_payload == {"summary": "finished in extension"}
    assert list_open_gates(state_dir, run_id) == []
    assert len(delegate_calls) == 2
    answers_after_replay = GateAnswerJournal.for_run(run_id, state_dir=state_dir).load_entries()
    assert len(answers_after_replay) == 1


def test_v3_heartbeat_expiry_is_transparent_and_node_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V3: heartbeat expiry mid-wait (deadline NOT exceeded) never fails the node.

    Two transport awaits expire before the third returns the artifact;
    with retry 0 the old semantics (timeout burns an attempt) would have
    parked — the demoted heartbeat re-awaits transparently and the node
    completes without follow-up noise.
    """
    workflow_path = tmp_path / "deadline_live.hy"
    state_dir = tmp_path / "state"
    run_id = "heartbeat-transparent"
    _write_deadline_workflow(workflow_path, deadline_seconds=30.0)

    session_id = _cheap_session_id(run_id, "deadline-live/0/agent")
    scenario_handler = ScenarioAgentHandler(
        scripts={
            session_id: [
                ScenarioStep.timeout(),
                ScenarioStep.timeout(),
                ScenarioStep.success({"summary": "finished late"}),
            ]
        },
    )
    runtime = MockConductorRuntime(tmp_path / "runtime")
    delegate_calls: list[str] = []
    _install_scenario_production_handlers(
        monkeypatch=monkeypatch,
        runtime=runtime,
        scenario_handler=scenario_handler,
        delegate_calls=delegate_calls,
    )

    handle = ConductorAPI(state_dir=state_dir).run_workflow(str(workflow_path), run_id=run_id)

    assert handle.status is WorkflowStatus.DONE
    assert handle.result_payload == {"summary": "finished late"}
    assert list_open_gates(state_dir, run_id) == []
    assert delegate_calls == ["deadline-live/0/agent"]
    # Transparent re-await: no retry prompt was injected into the session.
    assert scenario_handler.follow_up_messages(session_id) == []


def test_v3_run_state_records_no_gate_events_for_heartbeat_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V3 corollary: heartbeat expiry leaves no gate/park trace anywhere."""
    workflow_path = tmp_path / "deadline_live.hy"
    state_dir = tmp_path / "state"
    run_id = "heartbeat-no-trace"
    _write_deadline_workflow(workflow_path, deadline_seconds=30.0)

    session_id = _cheap_session_id(run_id, "deadline-live/0/agent")
    scenario_handler = ScenarioAgentHandler(
        scripts={
            session_id: [
                ScenarioStep.timeout(),
                ScenarioStep.success({"summary": "ok"}),
            ]
        },
    )
    runtime = MockConductorRuntime(tmp_path / "runtime")
    _install_scenario_production_handlers(
        monkeypatch=monkeypatch,
        runtime=runtime,
        scenario_handler=scenario_handler,
        delegate_calls=[],
    )

    handle = ConductorAPI(state_dir=state_dir).run_workflow(str(workflow_path), run_id=run_id)

    assert handle.status is WorkflowStatus.DONE
    journal_entries = AgentJournal.for_run(run_id, state_dir=state_dir).load_entries()
    assert [entry.terminal_kind for entry in journal_entries] == ["succeeded"]
    run_state_path = state_dir / "workflows" / run_id / "run-state.json"
    assert not run_state_path.exists() or not json.loads(
        run_state_path.read_text(encoding="utf-8")
    ).get("open_gates")
