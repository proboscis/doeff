"""Unit tests for workspace journal types and journaled_workspace_handler (L-K3-3)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from doeff_conductor.effects.workspace import CreateWorkspace
from doeff_conductor.exceptions import JournalCorruptionError
from doeff_conductor.handlers._workspace_path import WorkspacePath
from doeff_conductor.handlers.journaled_workspace import (
    PreCoverageRunError,
    journaled_workspace_handler,
)
from doeff_conductor.journal import (
    AGENT_JOURNAL_FILENAME,
    WORKSPACE_JOURNAL_FILENAME,
    CreateWorkspaceJournalEntry,
    WorkspaceJournal,
)
from doeff_conductor.types import Workspace

from doeff import K, Resume, handler, stacked_handlers
from doeff.program import ProgramHandler
from tests.effect_runner import Answer, answering


def _sample_entry(
    *,
    workspace_id: str = "ws-abc",
    repo: str = "default",
    branch: str = "conductor/ws-abc",
    worktree_path: str = "/tmp/ws-abc",
    base_ref: str = "main",
    issue_id: str | None = None,
    created_at: str = "2026-06-12T00:00:00+00:00",
) -> CreateWorkspaceJournalEntry:
    return CreateWorkspaceJournalEntry(
        workspace_id=workspace_id,
        repo=repo,
        branch=branch,
        worktree_path=worktree_path,
        base_ref=base_ref,
        issue_id=issue_id,
        created_at=created_at,
    )


def _sample_workspace(
    *,
    workspace_id: str = "ws-abc",
    repo: str = "default",
) -> Workspace:
    return Workspace(
        id=workspace_id,
        repo=repo,
        ref=f"conductor/{workspace_id}",
        base_ref="main",
        issue_id=None,
        created_at=datetime.now(timezone.utc),
    )


# =============================================================================
# CreateWorkspaceJournalEntry round-trip tests
# =============================================================================


class TestCreateWorkspaceJournalEntryRoundTrip:
    def test_round_trip_identity(self) -> None:
        entry: CreateWorkspaceJournalEntry = _sample_entry()
        json_line: str = entry.to_json_line()
        restored: CreateWorkspaceJournalEntry = CreateWorkspaceJournalEntry.from_json_line(
            json_line, path=Path("test.jsonl"), line_number=1
        )
        assert restored == entry

    def test_round_trip_with_issue_id(self) -> None:
        entry: CreateWorkspaceJournalEntry = _sample_entry(issue_id="ISSUE-001")
        json_line: str = entry.to_json_line()
        restored: CreateWorkspaceJournalEntry = CreateWorkspaceJournalEntry.from_json_line(
            json_line, path=Path("test.jsonl"), line_number=1
        )
        assert restored == entry
        assert restored.issue_id == "ISSUE-001"

    def test_round_trip_null_issue_id(self) -> None:
        entry: CreateWorkspaceJournalEntry = _sample_entry(issue_id=None)
        json_line: str = entry.to_json_line()
        restored: CreateWorkspaceJournalEntry = CreateWorkspaceJournalEntry.from_json_line(
            json_line, path=Path("test.jsonl"), line_number=1
        )
        assert restored.issue_id is None

    def test_invalid_json_raises_corruption(self) -> None:
        with pytest.raises(JournalCorruptionError, match="invalid JSON"):
            CreateWorkspaceJournalEntry.from_json_line(
                "{not valid json", path=Path("test.jsonl"), line_number=1
            )

    def test_non_object_raises_corruption(self) -> None:
        with pytest.raises(JournalCorruptionError, match="not a JSON object"):
            CreateWorkspaceJournalEntry.from_json_line(
                '"just a string"', path=Path("test.jsonl"), line_number=1
            )

    def test_missing_required_field_raises_corruption(self) -> None:
        payload: dict[str, Any] = {"version": 1, "workspace_id": "ws-x"}
        with pytest.raises(JournalCorruptionError, match="missing"):
            CreateWorkspaceJournalEntry.from_json_line(
                json.dumps(payload), path=Path("test.jsonl"), line_number=1
            )

    def test_wrong_version_raises_corruption(self) -> None:
        entry: CreateWorkspaceJournalEntry = _sample_entry()
        raw: dict[str, Any] = json.loads(entry.to_json_line())
        raw["version"] = 999
        with pytest.raises(JournalCorruptionError, match="unsupported journal version"):
            CreateWorkspaceJournalEntry.from_json_line(
                json.dumps(raw), path=Path("test.jsonl"), line_number=1
            )


# =============================================================================
# WorkspaceJournal tests
# =============================================================================


class TestWorkspaceJournal:
    def test_for_run_path_construction(self, tmp_path: Path) -> None:
        journal: WorkspaceJournal = WorkspaceJournal.for_run(
            "run-42", state_dir=tmp_path
        )
        expected: Path = tmp_path / "workflows" / "run-42" / WORKSPACE_JOURNAL_FILENAME
        assert journal.path == expected

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        journal: WorkspaceJournal = WorkspaceJournal.for_run(
            "run-empty", state_dir=tmp_path
        )
        assert journal.load_entries() == []

    def test_append_and_load(self, tmp_path: Path) -> None:
        journal: WorkspaceJournal = WorkspaceJournal.for_run(
            "run-append", state_dir=tmp_path
        )
        entry: CreateWorkspaceJournalEntry = _sample_entry()
        journal.append_entry(entry)

        loaded: list[CreateWorkspaceJournalEntry] = journal.load_entries()
        assert len(loaded) == 1
        assert loaded[0] == entry

    def test_latest_workspaces_last_wins(self, tmp_path: Path) -> None:
        journal: WorkspaceJournal = WorkspaceJournal.for_run(
            "run-lastwin", state_dir=tmp_path
        )
        first: CreateWorkspaceJournalEntry = _sample_entry(
            workspace_id="ws-1", worktree_path="/tmp/first"
        )
        second: CreateWorkspaceJournalEntry = _sample_entry(
            workspace_id="ws-1", worktree_path="/tmp/second"
        )
        third: CreateWorkspaceJournalEntry = _sample_entry(
            workspace_id="ws-2", worktree_path="/tmp/third"
        )
        journal.append_entry(first)
        journal.append_entry(second)
        journal.append_entry(third)

        latest: dict[str, CreateWorkspaceJournalEntry] = journal.latest_workspaces()
        assert len(latest) == 2
        assert latest["ws-1"].worktree_path == "/tmp/second"
        assert latest["ws-2"].worktree_path == "/tmp/third"

    def test_corrupt_json_raises(self, tmp_path: Path) -> None:
        journal: WorkspaceJournal = WorkspaceJournal.for_run(
            "run-corrupt", state_dir=tmp_path
        )
        journal.path.parent.mkdir(parents=True, exist_ok=True)
        journal.path.write_text("{invalid json\n", encoding="utf-8")

        with pytest.raises(JournalCorruptionError, match="invalid JSON"):
            journal.load_entries()

    def test_blank_line_raises(self, tmp_path: Path) -> None:
        journal: WorkspaceJournal = WorkspaceJournal.for_run(
            "run-blank", state_dir=tmp_path
        )
        entry: CreateWorkspaceJournalEntry = _sample_entry()
        journal.append_entry(entry)
        existing: str = journal.path.read_text(encoding="utf-8")
        journal.path.write_text(existing + "\n", encoding="utf-8")

        with pytest.raises(JournalCorruptionError, match="blank line"):
            journal.load_entries()


# =============================================================================
# journaled_workspace_handler tests
# =============================================================================


def _stub_workspace_handler(tracker: list[str]) -> ProgramHandler:
    """A workspace handler stand-in that records each creation it answers."""

    @handler
    def answer_workspace(effect: CreateWorkspace | WorkspacePath, k: K):
        match effect:
            case CreateWorkspace():
                tracker.append(effect.workspace_id)
                answer: object = _sample_workspace(
                    workspace_id=effect.workspace_id,
                    repo=effect.repo,
                )
            case WorkspacePath():
                answer = Path(f"/tmp/mock/{effect.workspace.id}")
        return (yield Resume(k, answer))

    return answer_workspace


def _journaled(tracker: list[str], state_dir: Path, run_id: str) -> Answer:
    """Run workspace effects through the journal installed inside the stub workspace handler."""
    return answering(
        stacked_handlers(
            _stub_workspace_handler(tracker),
            journaled_workspace_handler(state_dir=state_dir, run_id=run_id),
        )
    )


class TestJournaledWorkspaceHandler:
    def test_none_run_id_raises_at_construction(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="non-empty run_id"):
            journaled_workspace_handler(
                state_dir=tmp_path,
                run_id=None,  # type: ignore[arg-type] — the refusal of a missing run id is the subject
            )

    def test_empty_run_id_raises_at_construction(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="non-empty run_id"):
            journaled_workspace_handler(state_dir=tmp_path, run_id="")

    def test_first_call_delegates_and_journals(self, tmp_path: Path) -> None:
        tracker: list[str] = []
        answer: Answer = _journaled(tracker, tmp_path, "run-first")
        effect: CreateWorkspace = CreateWorkspace(workspace_id="ws-1", repo="default")
        result: Workspace = answer(effect)

        assert result.id == "ws-1"
        assert tracker == ["ws-1"]

        journal: WorkspaceJournal = WorkspaceJournal.for_run("run-first", state_dir=tmp_path)
        entries: list[CreateWorkspaceJournalEntry] = journal.load_entries()
        assert len(entries) == 1
        assert entries[0].workspace_id == "ws-1"
        assert entries[0].worktree_path == "/tmp/mock/ws-1"

    def test_second_call_same_workspace_does_not_double_append(self, tmp_path: Path) -> None:
        tracker: list[str] = []
        answer: Answer = _journaled(tracker, tmp_path, "run-dedup")
        effect: CreateWorkspace = CreateWorkspace(workspace_id="ws-dup", repo="default")
        answer(effect)
        answer(effect)

        assert tracker == ["ws-dup", "ws-dup"]

        journal: WorkspaceJournal = WorkspaceJournal.for_run("run-dedup", state_dir=tmp_path)
        entries: list[CreateWorkspaceJournalEntry] = journal.load_entries()
        assert len(entries) == 1

    def test_resumed_handler_does_not_re_journal(self, tmp_path: Path) -> None:
        tracker: list[str] = []
        effect: CreateWorkspace = CreateWorkspace(workspace_id="ws-resume", repo="default")
        _journaled(tracker, tmp_path, "run-resume")(effect)
        _journaled(tracker, tmp_path, "run-resume")(effect)

        assert tracker == ["ws-resume", "ws-resume"]

        journal: WorkspaceJournal = WorkspaceJournal.for_run("run-resume", state_dir=tmp_path)
        entries: list[CreateWorkspaceJournalEntry] = journal.load_entries()
        assert len(entries) == 1

    def test_pre_coverage_detection_raises(self, tmp_path: Path) -> None:
        run_dir: Path = tmp_path / "workflows" / "run-old"
        run_dir.mkdir(parents=True)
        (run_dir / AGENT_JOURNAL_FILENAME).write_text("", encoding="utf-8")

        tracker: list[str] = []
        answer: Answer = _journaled(tracker, tmp_path, "run-old")
        effect: CreateWorkspace = CreateWorkspace(workspace_id="ws-old", repo="default")

        with pytest.raises(PreCoverageRunError, match="predates workspace coverage"):
            answer(effect)
        assert tracker == []

    def test_pre_coverage_not_raised_when_no_agent_journal(self, tmp_path: Path) -> None:
        tracker: list[str] = []
        answer: Answer = _journaled(tracker, tmp_path, "run-fresh")
        effect: CreateWorkspace = CreateWorkspace(workspace_id="ws-fresh", repo="default")
        result: Workspace = answer(effect)
        assert result.id == "ws-fresh"

    def test_multiple_different_workspaces(self, tmp_path: Path) -> None:
        tracker: list[str] = []
        answer: Answer = _journaled(tracker, tmp_path, "run-multi")

        answer(CreateWorkspace(workspace_id="ws-a", repo="default"))
        answer(CreateWorkspace(workspace_id="ws-b", repo="default"))

        journal: WorkspaceJournal = WorkspaceJournal.for_run("run-multi", state_dir=tmp_path)
        entries: list[CreateWorkspaceJournalEntry] = journal.load_entries()
        assert len(entries) == 2
        workspace_ids: list[str] = [e.workspace_id for e in entries]
        assert workspace_ids == ["ws-a", "ws-b"]
