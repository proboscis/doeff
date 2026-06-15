"""ADR 0002 — progress producer + read-only monitor.

Pins the load-bearing invariants from the adversarial review:
  * D2 precedence: a validated agent-journal artifact wins over a stale progress
    label (blocked-pane-but-succeeded ⇒ DONE).
  * Fail-open emission (L-K4-1): a producer write failure never alters the run.
  * Resume isolation: replay reads the agent-journal, never the progress journal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from doeff_conductor import CreateWorkspace
from doeff_conductor.effects import Agent, AgentEffect, AgentTask
from doeff_conductor.handlers import run_sync
from doeff_conductor.handlers.journaled_agent import JournaledAgentHandler
from doeff_conductor.handlers.testing import MockConductorRuntime, mock_handlers
from doeff_conductor.journal import (
    PROGRESS_STATUS_PARKED,
    PROGRESS_STATUS_RUNNING,
    PROGRESS_STATUS_SUCCEEDED,
    TERMINAL_KIND_SUCCEEDED,
    AgentJournal,
    AgentJournalEntry,
    AgentReplaySession,
    ProgressJournal,
    ProgressJournalEntry,
)
from doeff_conductor.monitor import STATUS_DONE, node_status_map, render_run
from doeff_conductor.replay_keying import ResolvedIdentity

from doeff.do import do

ARTIFACT_SCHEMA = {
    "type": "object",
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
    "additionalProperties": False,
}


def _agent_task(*, run_id: str, node_id: str, env: Any, prompt: str, identity: ResolvedIdentity) -> AgentTask:
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
        phase="Implement",
        resolved_identity=identity,
        max_retries=0,
    )


def _session_id(run_id: str, node_id: str, identity: ResolvedIdentity) -> str:
    return _agent_task(run_id=run_id, node_id=node_id, env=None, prompt="", identity=identity).session_id


def _run_one_node(state_dir: Path, runtime: MockConductorRuntime, run_id: str, identity: ResolvedIdentity) -> Any:
    runtime.configure_agent_script(_session_id(run_id, "node-1", identity), [{"summary": "node-1 done"}])

    @do
    def workflow():
        env = yield CreateWorkspace(workspace_id="ws-1")
        result = yield Agent(
            _agent_task(run_id=run_id, node_id="node-1", env=env, prompt="do it", identity=identity)
        )
        return result

    journaled = JournaledAgentHandler(runtime.handle_agent, state_dir=state_dir, run_id=run_id)
    return run_sync(
        workflow(),
        scheduled_handlers=mock_handlers(runtime=runtime, overrides={AgentEffect: journaled.handle_agent}),
    )


# ----------------------------------------------------------------------------- #
# ProgressJournal mechanics
# ----------------------------------------------------------------------------- #


def test_progress_journal_roundtrip_and_latest(tmp_path: Path) -> None:
    journal = ProgressJournal(tmp_path / "progress-journal.jsonl")
    journal.append_entry(
        ProgressJournalEntry(
            node_id="n1", node_identity="i1", session_node_key="n1-aa", session_id="r-n1-aa-0",
            attempt=0, phase="P", status=PROGRESS_STATUS_RUNNING, terminal_kind=None, at="2026-06-15T00:00:00+00:00",
        )
    )
    journal.append_entry(
        ProgressJournalEntry(
            node_id="n1", node_identity="i1", session_node_key="n1-aa", session_id="r-n1-aa-0",
            attempt=0, phase="P", status=PROGRESS_STATUS_SUCCEEDED, terminal_kind=TERMINAL_KIND_SUCCEEDED,
            at="2026-06-15T00:00:01+00:00",
        )
    )
    latest = journal.latest_by_node()
    assert latest["n1"].status == PROGRESS_STATUS_SUCCEEDED


def test_progress_journal_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "progress-journal.jsonl"
    journal = ProgressJournal(path)
    journal.append_entry(
        ProgressJournalEntry(
            node_id="n1", node_identity="i1", session_node_key="n1-aa", session_id="s",
            attempt=0, phase=None, status=PROGRESS_STATUS_RUNNING, terminal_kind=None, at="t",
        )
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{ this is not json\n")
    # Observational stream: a corrupt line is skipped, never fatal.
    entries = journal.load_entries()
    assert len(entries) == 1
    assert entries[0].node_id == "n1"


# ----------------------------------------------------------------------------- #
# D2 precedence (the central law)
# ----------------------------------------------------------------------------- #


def test_d2_blocked_pane_but_succeeded_renders_done(tmp_path: Path) -> None:
    """A node still 'running' in the progress stream but with a validated
    agent-journal artifact MUST resolve to DONE (ADR 0002 D2 / ADR 0001 D6)."""
    state_dir = tmp_path / "state"
    run_id = "run-x"

    ProgressJournal.for_run(run_id, state_dir=state_dir).append_entry(
        ProgressJournalEntry(
            node_id="node-1", node_identity="ident-x", session_node_key="node-1-abcd1234",
            session_id="run-x-node-1-abcd1234-0", attempt=0, phase="Implement",
            status=PROGRESS_STATUS_RUNNING, terminal_kind=None, at="2026-06-15T00:00:00+00:00",
        )
    )
    AgentJournal.for_run(run_id, state_dir=state_dir).append_entry(
        AgentJournalEntry(
            generation=1, entry_index=0, cache_key="k", resolved_identity_fingerprint="f",
            node_identity="ident-x", result_artifact={"summary": "ok"}, terminal_kind=TERMINAL_KIND_SUCCEEDED,
        )
    )

    views = node_status_map(state_dir, run_id)
    assert views["node-1"].status == STATUS_DONE


def test_d2_running_stays_running_without_artifact(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = "run-y"
    ProgressJournal.for_run(run_id, state_dir=state_dir).append_entry(
        ProgressJournalEntry(
            node_id="node-1", node_identity="ident-y", session_node_key="node-1-aa",
            session_id="run-y-node-1-aa-0", attempt=0, phase="Implement",
            status=PROGRESS_STATUS_RUNNING, terminal_kind=None, at="t",
        )
    )
    views = node_status_map(state_dir, run_id)
    assert views["node-1"].status == PROGRESS_STATUS_RUNNING


# ----------------------------------------------------------------------------- #
# Producer integration + fail-open + resume isolation
# ----------------------------------------------------------------------------- #


def test_producer_emits_running_then_succeeded(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    runtime = MockConductorRuntime(tmp_path / "runtime")
    identity = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company")
    run_id = "run-prod"

    result = _run_one_node(state_dir, runtime, run_id, identity)
    assert result.is_ok

    progress = ProgressJournal.for_run(run_id, state_dir=state_dir).load_entries()
    statuses = [e.status for e in progress if e.node_id == "node-1"]
    assert PROGRESS_STATUS_RUNNING in statuses
    assert PROGRESS_STATUS_SUCCEEDED in statuses
    # identity join: the progress event's node_identity matches the agent-journal.
    agent_entries = AgentJournal.for_run(run_id, state_dir=state_dir).latest_generation_entries()
    succeeded_ident = next(e.node_identity for e in agent_entries if e.terminal_kind == TERMINAL_KIND_SUCCEEDED)
    assert any(e.node_identity == succeeded_ident for e in progress)
    # session_id matches the deterministic runtime derivation (attach target).
    assert any(e.session_id == _session_id(run_id, "node-1", identity) for e in progress)


def test_emission_is_fail_open(tmp_path: Path, monkeypatch: Any) -> None:
    """A progress-journal write that raises must NOT alter the run (L-K4-1)."""
    state_dir = tmp_path / "state"
    runtime = MockConductorRuntime(tmp_path / "runtime")
    identity = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company")
    run_id = "run-failopen"

    def boom(self: Any, entry: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(ProgressJournal, "append_entry", boom)

    result = _run_one_node(state_dir, runtime, run_id, identity)
    # Run still succeeds; the agent-journal (store of record) is intact.
    assert result.is_ok
    agent_entries = AgentJournal.for_run(run_id, state_dir=state_dir).latest_generation_entries()
    assert any(e.terminal_kind == TERMINAL_KIND_SUCCEEDED for e in agent_entries)


def test_resume_reads_agent_journal_not_progress_journal(tmp_path: Path) -> None:
    """Replay depends only on the agent-journal; a corrupt progress journal is
    irrelevant to resume (ADR 0002 D1 observational isolation)."""
    state_dir = tmp_path / "state"
    runtime = MockConductorRuntime(tmp_path / "runtime")
    identity = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company")
    run_id = "run-resume"

    assert _run_one_node(state_dir, runtime, run_id, identity).is_ok

    # Corrupt the observational progress journal entirely.
    progress_path = ProgressJournal.for_run(run_id, state_dir=state_dir).path
    progress_path.write_text("garbage not json\n{also broken\n", encoding="utf-8")

    # The replay cursor still loads the cached prefix from the agent-journal.
    session = AgentReplaySession(AgentJournal.for_run(run_id, state_dir=state_dir))
    assert len(session.previous_entries) == 1
    assert session.previous_entries[0].terminal_kind == TERMINAL_KIND_SUCCEEDED


# ----------------------------------------------------------------------------- #
# Render smoke
# ----------------------------------------------------------------------------- #


def test_render_run_shows_done_glyph(tmp_path: Path) -> None:
    from rich.console import Console

    state_dir = tmp_path / "state"
    run_id = "run-render"
    ProgressJournal.for_run(run_id, state_dir=state_dir).append_entry(
        ProgressJournalEntry(
            node_id="node-1", node_identity="ident-r", session_node_key="node-1-aa",
            session_id="s", attempt=0, phase="Implement",
            status=PROGRESS_STATUS_RUNNING, terminal_kind=None, at="t",
        )
    )
    AgentJournal.for_run(run_id, state_dir=state_dir).append_entry(
        AgentJournalEntry(
            generation=1, entry_index=0, cache_key="k", resolved_identity_fingerprint="f",
            node_identity="ident-r", result_artifact={"summary": "ok"}, terminal_kind=TERMINAL_KIND_SUCCEEDED,
        )
    )

    console = Console(record=True, width=120)
    console.print(render_run(state_dir, run_id, name="render-run", run_status="running"))
    text = console.export_text()
    assert "node-1" in text
    assert "Implement" in text
    assert "✓" in text  # DONE glyph (D2 precedence applied)


def test_node_tree_falls_back_to_open_gates_without_progress_journal(tmp_path: Path) -> None:
    """A run with parked gates but no progress journal (e.g. created before the
    producer) still shows its parked nodes in the tree (not an empty tree)."""
    from doeff_conductor.overseer import GateOption, OpenGateView, record_open_gates

    state_dir = tmp_path / "state"
    run_id = "run-gatefallback"
    node = "Build/0/parallel[0]/agent"
    record_open_gates(
        state_dir,
        workflow_id=run_id,
        workflow_name="wf",
        supervision="autonomous",
        open_gates=(
            OpenGateView(
                gate_id="g0", workflow_id=run_id, node_id=node, phase="Build",
                reason="budget-exhausted", stakes={},
                options=(GateOption(name="proceed", outcome="resume", description="d"),),
            ),
        ),
    )
    # No progress journal exists for this run.
    views = node_status_map(state_dir, run_id)
    assert node in views
    assert views[node].status == PROGRESS_STATUS_PARKED
    assert views[node].phase == "Build"
