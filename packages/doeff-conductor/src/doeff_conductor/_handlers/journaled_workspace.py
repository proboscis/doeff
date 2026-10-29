"""Journal-backed CreateWorkspace handler wrapper (L-K3-3).

Records workspace materializations in a durable journal so that resumed
runs can verify coverage: every workspace that existed in the original
run must still exist in the resumed run.
"""


import functools
from datetime import datetime, timezone
from pathlib import Path

from doeff import K, Resume, handler
from doeff.program import ProgramHandler
from doeff_conductor._effects.workspace import CreateWorkspace
from doeff_conductor._handlers._workspace_path import WorkspacePath
from doeff_conductor._journal import (
    AGENT_JOURNAL_FILENAME,
    CreateWorkspaceJournalEntry,
    WorkspaceJournal,
)
from doeff_conductor._types import Workspace

__all__ = ["PreCoverageRunError", "journaled_workspace_handler"]


class PreCoverageRunError(RuntimeError):
    """Raised when a pre-coverage run is detected on resume.

    An agent journal exists but no workspace journal does, meaning the
    original run predates workspace coverage.  Continuing would silently
    create fresh worktrees while agents re-adopt stale session names.
    """


def _check_pre_coverage(journal: WorkspaceJournal, run_id: str) -> None:
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


def journaled_workspace_handler(
    *,
    run_id: str,
    state_dir: str | Path | None = None,
) -> ProgramHandler:
    """Build the handler that records workspace creation events in a durable journal.

    It catches ``CreateWorkspace``, performs it outward — the handler is installed inside the
    workspace handler (``workspace_handler``, or a test's) — and appends a journal entry with the
    worktree path (``WorkspacePath``) for each **new** workspace_id materialization. Re-emitting
    the same workspace_id (resume) performs the effect again but does not double-append.

    Args:
        run_id: The run whose workspace journal is written. Required: journaling into an
            unknown run directory is a silent misconfiguration.
        state_dir: The conductor state directory. The XDG default if not provided.

    Raises:
        PreCoverageRunError: on the first workspace effect of a run that has an agent journal
            but no workspace journal.
    """
    if not run_id:
        raise ValueError(
            "journaled_workspace_handler requires a non-empty run_id; "
            "journaling into an unknown run directory is a silent misconfiguration"
        )
    journal_dir: Path | None = Path(state_dir) if state_dir is not None else None

    @functools.cache
    def journal() -> WorkspaceJournal:
        """The run's workspace journal, checked for pre-coverage runs when first opened."""
        opened = WorkspaceJournal.for_run(run_id, state_dir=journal_dir)
        _check_pre_coverage(opened, run_id)
        return opened

    @handler
    def journal_workspace(effect: CreateWorkspace, k: K):
        """Perform the workspace creation outward, then journal it if this is a new workspace_id."""
        known_workspaces: dict[str, CreateWorkspaceJournalEntry] = journal().latest_workspaces()

        workspace: Workspace = yield effect

        if effect.workspace_id not in known_workspaces:
            worktree_path: Path = yield WorkspacePath(workspace=workspace)
            journal().append_entry(
                CreateWorkspaceJournalEntry(
                    workspace_id=workspace.id,
                    repo=workspace.repo,
                    branch=workspace.ref,
                    worktree_path=str(worktree_path),
                    base_ref=workspace.base_ref,
                    issue_id=workspace.issue_id,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )

        return (yield Resume(k, workspace))

    return journal_workspace
