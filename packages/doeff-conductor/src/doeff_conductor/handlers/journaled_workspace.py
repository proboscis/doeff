"""Journal-backed CreateWorkspace handler wrapper (L-K3-3).

Records workspace materializations in a durable journal so that resumed
runs can verify coverage: every workspace that existed in the original
run must still exist in the resumed run.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from doeff_conductor.journal import (
    AGENT_JOURNAL_FILENAME,
    CreateWorkspaceJournalEntry,
    WorkspaceJournal,
)
from doeff_conductor.types import Workspace

if TYPE_CHECKING:
    from doeff_conductor.effects.workspace import CreateWorkspace


class PreCoverageRunError(RuntimeError):
    """Raised when a pre-coverage run is detected on resume.

    An agent journal exists but no workspace journal does, meaning the
    original run predates workspace coverage.  Continuing would silently
    create fresh worktrees while agents re-adopt stale session names.
    """


class JournaledWorkspaceHandler:
    """Record workspace creation events in a durable journal.

    Wraps a delegate workspace handler (real or mock) and appends a
    journal entry for each **new** workspace_id materialization.
    Re-emitting the same workspace_id (resume) delegates but does
    not double-append.
    """

    def __init__(
        self,
        delegate: Callable[[CreateWorkspace], Workspace],
        *,
        state_dir: str | Path | None = None,
        run_id: str,
        resolve_path: Callable[[Workspace], Path] | None = None,
    ) -> None:
        if not run_id:
            raise ValueError(
                "JournaledWorkspaceHandler requires a non-empty run_id; "
                "journaling into an unknown run directory is a silent misconfiguration"
            )
        self._delegate = delegate
        self._state_dir = Path(state_dir) if state_dir is not None else None
        self._run_id = run_id
        self._resolve_path = resolve_path
        self._journals: dict[str, WorkspaceJournal] = {}

    def _journal_for(self, run_id: str) -> WorkspaceJournal:
        journal: WorkspaceJournal | None = self._journals.get(run_id)
        if journal is not None:
            return journal
        journal = WorkspaceJournal.for_run(
            run_id,
            state_dir=self._state_dir,
        )
        self._check_pre_coverage(journal, run_id)
        self._journals[run_id] = journal
        return journal

    def _check_pre_coverage(self, journal: WorkspaceJournal, run_id: str) -> None:
        """Fail loudly if the run predates workspace coverage."""
        if journal.path.exists():
            return
        agent_journal_path: Path = journal.path.parent / AGENT_JOURNAL_FILENAME
        if agent_journal_path.exists():
            raise PreCoverageRunError(
                f"Run {run_id!r} has an agent journal but no workspace journal. "
                f"This run predates workspace coverage (L-K3-3). "
                f"Start a fresh run with a new --run-id."
            )

    def handle_create_workspace(self, effect: CreateWorkspace) -> Workspace:
        """Delegate to real handler, then journal if this is a new workspace_id."""
        run_id: str = self._run_id
        journal: WorkspaceJournal = self._journal_for(run_id)
        known_workspaces: dict[str, CreateWorkspaceJournalEntry] = journal.latest_workspaces()

        workspace: Workspace = self._delegate(effect)

        if effect.workspace_id not in known_workspaces:
            worktree_path: str = self._resolve_worktree_path(workspace)
            journal.append_entry(
                CreateWorkspaceJournalEntry(
                    workspace_id=workspace.id,
                    repo=workspace.repo,
                    branch=workspace.ref,
                    worktree_path=worktree_path,
                    base_ref=workspace.base_ref,
                    issue_id=workspace.issue_id,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )

        return workspace

    def _resolve_worktree_path(self, workspace: Workspace) -> str:
        if self._resolve_path is not None:
            return str(self._resolve_path(workspace))
        return ""
