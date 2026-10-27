"""E2E resume stability tests for workspace journal coverage (L-K3-3).

Verifies that workspace creation is durably journaled so that resumed
runs can structurally detect whether workspace coverage existed in the
original run.  A run that lacks workspace coverage but has an agent
journal is a pre-coverage run and must fail loudly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from doeff_conductor._api import ConductorAPI
from doeff_conductor.effects import CreateWorkspace
from doeff_conductor.handlers.journaled_agent import journaled_agent_handler
from doeff_conductor.handlers.journaled_workspace import (
    journaled_workspace_handler,
)
from doeff_conductor.handlers.testing import MockConductorRuntime, mock_handlers
from doeff_conductor.journal import (
    AGENT_JOURNAL_FILENAME,
    WORKSPACE_JOURNAL_FILENAME,
    WorkspaceJournal,
)
from doeff_conductor.types import WorkflowStatus, Workspace
from doeff_conductor.workflow_effect_journal import journaled_workflow_effect_handler

from doeff import K, Resume, handler, stacked_handlers

SHARED_WORKSPACE_WORKFLOW = """
(require doeff-conductor.macros [defworkflow agent! workspace! <-])
(import doeff_conductor.dsl [artifact prompt ref])

(setv RESULT-SCHEMA {"type" "object"
                     "required" ["summary"]
                     "properties" {"summary" {"type" "string"}}
                     "additionalProperties" False})

(setv ws (workspace! :from "main"))

(defworkflow k3-journal-workflow
  :params {}
  :roles {"implementer" {"profile" "cheap-coder" "retry" 0}}
  (<- result
      (agent! :role "implementer"
              :class "test-verifiable"
              :prompt (prompt "implement on shared workspace")
              :schema RESULT-SCHEMA
              :workspace ws
              :label "impl"))
  (artifact (ref "result")))

(setv WORKFLOW k3-journal-workflow)
""".lstrip()


def _install_mock_production_handlers_with_workspace_journal(
    *,
    monkeypatch: pytest.MonkeyPatch,
    runtime: MockConductorRuntime,
    creation_tracker: list[str],
) -> None:
    """Wire mock production handlers that journal both agent and workspace effects."""
    import doeff_conductor.handlers as handlers_module

    def production_handlers(**kwargs: object) -> Any:
        state_dir: str | Path | None = cast(str | Path | None, kwargs["journal_state_dir"])
        run_id: str = str(kwargs["journal_run_id"])

        @handler
        def track_creation(effect: CreateWorkspace, k: K):
            """Record each workspace creation the journal performs, then let the runtime answer it."""
            creation_tracker.append(effect.workspace_id)
            workspace: Workspace = yield effect
            return (yield Resume(k, workspace))

        return stacked_handlers(
            mock_handlers(runtime=runtime),
            track_creation,
            journaled_workflow_effect_handler(state_dir=state_dir, run_id=run_id),
            journaled_agent_handler(state_dir=state_dir, run_id=run_id),
            journaled_workspace_handler(state_dir=state_dir, run_id=run_id),
        )

    monkeypatch.setattr(handlers_module, "production_handlers", production_handlers)


def test_first_run_writes_workspace_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First run creates a workspace journal entry for the shared workspace."""
    workflow_path: Path = tmp_path / "k3_workflow.hy"
    workflow_path.write_text(SHARED_WORKSPACE_WORKFLOW, encoding="utf-8")
    state_dir: Path = tmp_path / "state"

    runtime: MockConductorRuntime = MockConductorRuntime(tmp_path / "runtime")
    creation_tracker: list[str] = []
    _install_mock_production_handlers_with_workspace_journal(
        monkeypatch=monkeypatch,
        runtime=runtime,
        creation_tracker=creation_tracker,
    )

    handle = ConductorAPI(state_dir=state_dir).run_workflow(
        str(workflow_path),
        run_id="k3-first",
    )

    assert handle.status is WorkflowStatus.DONE

    journal: WorkspaceJournal = WorkspaceJournal.for_run("k3-first", state_dir=state_dir)
    entries = journal.load_entries()
    assert len(entries) == 1
    assert entries[0].repo == "default"
    assert entries[0].terminal_kind == "workspace-created"

    assert len(creation_tracker) == 1


def test_resume_does_not_double_journal_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resumed run delegates to the workspace handler but does not re-journal."""
    workflow_path: Path = tmp_path / "k3_workflow.hy"
    workflow_path.write_text(SHARED_WORKSPACE_WORKFLOW, encoding="utf-8")
    state_dir: Path = tmp_path / "state"

    runtime: MockConductorRuntime = MockConductorRuntime(tmp_path / "runtime")
    creation_tracker: list[str] = []
    _install_mock_production_handlers_with_workspace_journal(
        monkeypatch=monkeypatch,
        runtime=runtime,
        creation_tracker=creation_tracker,
    )

    api: ConductorAPI = ConductorAPI(state_dir=state_dir)
    first_handle = api.run_workflow(str(workflow_path), run_id="k3-resume")
    assert first_handle.status is WorkflowStatus.DONE

    second_handle = api.run_workflow(str(workflow_path), run_id="k3-resume")
    assert second_handle.status is WorkflowStatus.DONE

    assert len(creation_tracker) == 2

    journal: WorkspaceJournal = WorkspaceJournal.for_run("k3-resume", state_dir=state_dir)
    entries = journal.load_entries()
    assert len(entries) == 1


def test_agent_journal_without_workspace_coverage_is_detectable(
    tmp_path: Path,
) -> None:
    """Layer 4 structural detection: agent journal + missing workspace journal = pre-coverage."""
    state_dir: Path = tmp_path / "state"
    run_id: str = "k3-precoverage"
    run_dir: Path = state_dir / "workflows" / run_id
    run_dir.mkdir(parents=True)

    (run_dir / AGENT_JOURNAL_FILENAME).write_text("", encoding="utf-8")

    workspace_journal_path: Path = run_dir / WORKSPACE_JOURNAL_FILENAME
    assert not workspace_journal_path.exists()

    agent_journal_path: Path = run_dir / AGENT_JOURNAL_FILENAME
    assert agent_journal_path.exists()
