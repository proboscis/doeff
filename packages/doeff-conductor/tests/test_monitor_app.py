"""ADR 0002 — interactive (Textual) browser smoke test via the run-test pilot."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from doeff_conductor.api import ConductorAPI
from doeff_conductor.journal import (
    PROGRESS_STATUS_RUNNING,
    TERMINAL_KIND_SUCCEEDED,
    AgentJournal,
    AgentJournalEntry,
    ProgressJournal,
    ProgressJournalEntry,
)
from doeff_conductor.monitor_app import MonitorApp
from doeff_conductor.types import WorkflowHandle, WorkflowStatus
from textual.widgets import DataTable, Tree


def _fixture(tmp_path: Path) -> tuple[Path, str]:
    state = tmp_path / "state"
    run = "demo"
    now = datetime.now(timezone.utc)
    ConductorAPI(str(state))._save_workflow(
        WorkflowHandle(
            id=run, name="demo", status=WorkflowStatus.RUNNING, template=None,
            issue_id=None, created_at=now, updated_at=now,
        )
    )
    pj = ProgressJournal.for_run(run, state_dir=state)
    pj.append_entry(ProgressJournalEntry(
        node_id="Build/0/a/agent", node_identity="ia", session_node_key="a",
        session_id="demo-a-0", attempt=0, phase="Build",
        status=PROGRESS_STATUS_RUNNING, terminal_kind=None, at="t",
    ))
    pj.append_entry(ProgressJournalEntry(
        node_id="Build/0/b/agent", node_identity="ib", session_node_key="b",
        session_id="demo-b-0", attempt=0, phase="Build",
        status=PROGRESS_STATUS_RUNNING, terminal_kind=None, at="t",
    ))
    # node b has a validated artifact -> D2 precedence renders it DONE.
    AgentJournal.for_run(run, state_dir=state).append_entry(AgentJournalEntry(
        generation=1, entry_index=0, cache_key="k", resolved_identity_fingerprint="f",
        node_identity="ib", result_artifact={"summary": "ok"}, terminal_kind=TERMINAL_KIND_SUCCEEDED,
    ))
    return state, run


@pytest.mark.asyncio
async def test_app_browses_runs_and_expands_workflow(tmp_path: Path) -> None:
    state, _run = _fixture(tmp_path)
    app = MonitorApp(str(state), interval=999.0)  # no auto-tick churn during the test
    async with app.run_test() as pilot:
        await pilot.pause()

        # Left: runs list populated with our one run.
        table = app.query_one("#runs", DataTable)
        assert table.row_count == 1

        # Center: workflow expanded into one phase (Build) with two nodes.
        tree = app.query_one("#tree", Tree)
        assert len(tree.root.children) == 1
        build = tree.root.children[0]
        assert build.label.plain.strip() == "Build"
        assert len(build.children) == 2

        labels = sorted(leaf.label.plain for leaf in build.children)
        # node a is running; node b resolves to DONE via the agent-journal (D2).
        assert any("a" in lbl and "running" in lbl for lbl in labels)
        assert any("b" in lbl and "done" in lbl for lbl in labels)


@pytest.mark.asyncio
async def test_app_handles_no_runs(tmp_path: Path) -> None:
    app = MonitorApp(str(tmp_path / "empty"), interval=999.0)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#runs", DataTable).row_count == 0
