from __future__ import annotations

from pathlib import Path
from typing import Any

from doeff_agents import (
    AgentTask as L2AgentTask,
)
from doeff_agents import (
    AgentType,
    AwaitResultEffect,
    AwaitStatus,
    LaunchSessionEffect,
)
from doeff_agents.handlers.testing import ScenarioAgentHandler, ScenarioStep
from doeff_conductor import CreateWorkspace
from doeff_conductor.effects import Agent, AgentEffect, AgentTask
from doeff_conductor.exceptions import JournalCorruptionError
from doeff_conductor.handlers import run_sync
from doeff_conductor.handlers.journaled_agent import JournaledAgentHandler
from doeff_conductor.handlers.testing import MockConductorRuntime, mock_handlers
from doeff_conductor.journal import AgentJournal
from doeff_conductor.replay_keying import ResolvedIdentity

from doeff_core_effects.scheduler import scheduled
from doeff.do import do
from doeff.run import run

ARTIFACT_SCHEMA = {
    "type": "object",
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
    "additionalProperties": False,
}


def _agent_task(
    *,
    run_id: str,
    node_id: str,
    env: Any,
    prompt: str,
    identity: ResolvedIdentity,
) -> AgentTask:
    return AgentTask(
        run_id=run_id,
        node_id=node_id,
        attempt=0,
        env=env,
        prompt=prompt,
        result_schema=ARTIFACT_SCHEMA,
        verification_class="test-verifiable",
        agent_type=identity.adapter,
        model=identity.model,
        profile="company",
        resolved_identity=identity,
        max_retries=0,
    )


def _run_with_journal(
    program: Any,
    *,
    runtime: MockConductorRuntime,
    state_dir: Path,
) -> Any:
    journaled_handler = JournaledAgentHandler(
        runtime.handle_agent,
        state_dir=state_dir,
    )
    return run_sync(
        program,
        scheduled_handlers=mock_handlers(
            runtime=runtime,
            overrides={AgentEffect: journaled_handler.handle_agent},
        ),
    )



def _session_id(run_id: str, node_id: str, identity: ResolvedIdentity) -> str:
    """Derive the session id exactly as the runtime does (incl. fingerprint)."""
    return _agent_task(
        run_id=run_id, node_id=node_id, env=None, prompt="", identity=identity
    ).session_id

def test_kill_and_resume_replays_longest_valid_prefix_under_stubs(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    runtime = MockConductorRuntime(tmp_path / "runtime")
    identity = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company")
    run_id = "run-c3"

    for node_id in ("node-1", "node-2", "node-3"):
        runtime.configure_agent_script(
            _session_id(run_id, node_id, identity),
            [{"summary": f"{node_id} executed"}],
        )

    @do
    def workflow(*, crash_after_second: bool):
        env = yield CreateWorkspace(workspace_id="ws-c3")
        first = yield Agent(
            _agent_task(
                run_id=run_id,
                node_id="node-1",
                env=env,
                prompt="node one",
                identity=identity,
            )
        )
        second = yield Agent(
            _agent_task(
                run_id=run_id,
                node_id="node-2",
                env=env,
                prompt="node two",
                identity=identity,
            )
        )
        if crash_after_second:
            raise RuntimeError("simulated kill after node 2")
        third = yield Agent(
            _agent_task(
                run_id=run_id,
                node_id="node-3",
                env=env,
                prompt="node three",
                identity=identity,
            )
        )
        return first, second, third

    first_result = _run_with_journal(
        workflow(crash_after_second=True),
        runtime=runtime,
        state_dir=state_dir,
    )

    assert first_result.is_err()
    assert runtime.agent_invocation_count(_session_id(run_id, "node-1", identity)) == 1
    assert runtime.agent_invocation_count(_session_id(run_id, "node-2", identity)) == 1
    assert runtime.agent_invocation_count(_session_id(run_id, "node-3", identity)) == 0

    journal = AgentJournal.for_run(run_id, state_dir=state_dir)
    assert journal.path == state_dir / "workflows" / run_id / "agent-journal.jsonl"
    assert [entry.result_artifact["summary"] for entry in journal.latest_generation_entries()] == [
        "node-1 executed",
        "node-2 executed",
    ]

    resumed_result = _run_with_journal(
        workflow(crash_after_second=False),
        runtime=runtime,
        state_dir=state_dir,
    )

    assert resumed_result.is_ok()
    assert [artifact["summary"] for artifact in resumed_result.value] == [
        "node-1 executed",
        "node-2 executed",
        "node-3 executed",
    ]
    assert runtime.agent_invocation_count(_session_id(run_id, "node-1", identity)) == 1
    assert runtime.agent_invocation_count(_session_id(run_id, "node-2", identity)) == 1
    assert runtime.agent_invocation_count(_session_id(run_id, "node-3", identity)) == 1


def test_fingerprint_mismatch_invalidates_cached_agent_result(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    runtime = MockConductorRuntime(tmp_path / "runtime")
    run_id = "run-fingerprint"

    @do
    def workflow(identity: ResolvedIdentity):
        env = yield CreateWorkspace(workspace_id="ws-fingerprint")
        return (
            yield Agent(
                _agent_task(
                    run_id=run_id,
                    node_id="node-1",
                    env=env,
                    prompt="same author prompt",
                    identity=identity,
                )
            )
        )

    old_identity = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company-v1")
    new_identity = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company-v2")
    # A changed resolved identity is a DIFFERENT session, not a re-prompt
    # of the old one: name-only re-adoption used to serve the stale
    # payload after the journal had correctly invalidated (observed live).
    runtime.configure_agent_script(
        _session_id(run_id, "node-1", old_identity), [{"summary": "old profile result"}]
    )
    runtime.configure_agent_script(
        _session_id(run_id, "node-1", new_identity), [{"summary": "new profile result"}]
    )

    first_result = _run_with_journal(
        workflow(old_identity),
        runtime=runtime,
        state_dir=state_dir,
    )
    second_result = _run_with_journal(
        workflow(new_identity),
        runtime=runtime,
        state_dir=state_dir,
    )

    assert first_result.value == {"summary": "old profile result"}
    assert second_result.value == {"summary": "new profile result"}
    assert runtime.agent_invocation_count(_session_id(run_id, "node-1", old_identity)) == 1
    assert runtime.agent_invocation_count(_session_id(run_id, "node-1", new_identity)) == 1

    latest_entries = AgentJournal.for_run(run_id, state_dir=state_dir).latest_generation_entries()
    assert len(latest_entries) == 1
    assert latest_entries[0].result_artifact == {"summary": "new profile result"}


def test_corrupt_agent_journal_fails_with_typed_error(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    runtime = MockConductorRuntime(tmp_path / "runtime")
    run_id = "run-corrupt"
    journal = AgentJournal.for_run(run_id, state_dir=state_dir)
    journal.path.parent.mkdir(parents=True)
    journal.path.write_text("{not json}\n", encoding="utf-8")

    @do
    def workflow():
        env = yield CreateWorkspace(workspace_id="ws-corrupt")
        return (
            yield Agent(
                _agent_task(
                    run_id=run_id,
                    node_id="node-1",
                    env=env,
                    prompt="will not run",
                    identity=ResolvedIdentity(
                        adapter="codex",
                        model="gpt-5",
                        identity="company",
                    ),
                )
            )
        )

    result = _run_with_journal(
        workflow(),
        runtime=runtime,
        state_dir=state_dir,
    )

    assert result.is_err()
    assert isinstance(result.error, JournalCorruptionError)
    assert runtime.agent_invocation_count(f"{run_id}-node-1-0") == 0


def test_l2_launch_re_adopts_session_by_deterministic_id_after_interrupted_await(
    tmp_path: Path,
) -> None:
    task = L2AgentTask(
        run_id="run-re-adopt",
        node_id="node-1",
        attempt=0,
        agent_type=AgentType.CODEX,
        work_dir=tmp_path,
        prompt="produce result",
        result_schema=ARTIFACT_SCHEMA,
        max_retries=0,
    )
    handler = ScenarioAgentHandler(
        scripts={
            task.session_id: [
                ScenarioStep.timeout(),
                ScenarioStep.success({"summary": "resumed result"}),
            ]
        }
    )

    @do
    def launch_and_await():
        handle = yield LaunchSessionEffect(spec=task)
        outcome = yield AwaitResultEffect(handle=handle, timeout_seconds=0.0)
        return handle.session_id, outcome

    first_session_id, first_outcome = run(scheduled(handler.wrap(launch_and_await())))
    second_session_id, second_outcome = run(scheduled(handler.wrap(launch_and_await())))

    assert first_session_id == "run-re-adopt-node-1-0"
    assert second_session_id == first_session_id
    assert first_outcome.status == AwaitStatus.TIMED_OUT
    assert second_outcome.status == AwaitStatus.EXITED
    assert second_outcome.result == {"summary": "resumed result"}
    assert handler.launch_count(task.session_id) == 1
