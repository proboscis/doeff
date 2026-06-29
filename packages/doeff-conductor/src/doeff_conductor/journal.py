"""Durable L3 agent effect journal for conductor workflow replay."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doeff_agents.result_validation import validate_result_payload

from doeff_conductor.effects.agent import (
    AgentAttemptExhaustedError,
    AgentDeadlineExceededError,
    AgentEffect,
    AgentTask,
)
from doeff_conductor.exceptions import AgentError, JournalCorruptionError
from doeff_conductor.replay_keying import (
    ResolvedIdentity,
    agent_cache_key,
    longest_valid_prefix,
    node_identity_fingerprint,
    resolved_identity_fingerprint,
)

JOURNAL_VERSION = 1
AGENT_JOURNAL_FILENAME = "agent-journal.jsonl"
GATE_ANSWER_JOURNAL_FILENAME = "gate-answer-journal.jsonl"
WORKSPACE_JOURNAL_FILENAME = "workspace-journal.jsonl"
# ADR 0002: write-only OBSERVATIONAL node-lifecycle stream. Resume/replay MUST
# NOT read this file; its presence/absence changes no run outcome (it is kept
# out of the K3/K5 store-of-record). Consumed only by the read-only monitor.
PROGRESS_JOURNAL_FILENAME = "progress-journal.jsonl"
TERMINAL_KIND_SUCCEEDED = "succeeded"

logger = logging.getLogger(__name__)

# ADR 0002 D2: node-lifecycle status vocabulary (distinct from agentd pane
# liveness, which is never a completion/correctness source).
PROGRESS_STATUS_RUNNING = "running"
PROGRESS_STATUS_SUCCEEDED = "succeeded"
PROGRESS_STATUS_FAILED = "failed"
PROGRESS_STATUS_PARKED = "parked"
TERMINAL_KIND_OPEN_GATE = "open-gate"
TERMINAL_KIND_GATE_ANSWER = "gate-answer"
TERMINAL_KIND_WORKSPACE_CREATED = "workspace-created"


@dataclass(frozen=True, kw_only=True)
class AgentJournalEntry:
    """One append-only L3 agent journal record."""

    generation: int
    entry_index: int
    cache_key: str
    resolved_identity_fingerprint: str
    node_identity: str
    result_artifact: Any
    terminal_kind: str

    def to_json_line(self) -> str:
        payload: dict[str, Any] = {
            "version": JOURNAL_VERSION,
            "generation": self.generation,
            "entry_index": self.entry_index,
            "cache_key": self.cache_key,
            "resolved_identity_fingerprint": self.resolved_identity_fingerprint,
            "node_identity": self.node_identity,
            "result_artifact": self.result_artifact,
            "terminal_kind": self.terminal_kind,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @classmethod
    def from_json_line(cls, line: str, *, path: Path, line_number: int) -> AgentJournalEntry:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise JournalCorruptionError(
                path=path,
                message=f"invalid JSON on line {line_number}: {exc.msg}",
            ) from exc

        if not isinstance(payload, dict):
            raise JournalCorruptionError(
                path=path,
                message=f"line {line_number} is not a JSON object",
            )
        version = _require_int(payload, "version", path, line_number)
        if version != JOURNAL_VERSION:
            raise JournalCorruptionError(
                path=path,
                message=f"line {line_number} has unsupported journal version {version}",
            )

        return cls(
            generation=_require_int(payload, "generation", path, line_number),
            entry_index=_require_int(payload, "entry_index", path, line_number),
            cache_key=_require_str(payload, "cache_key", path, line_number),
            resolved_identity_fingerprint=_require_str(
                payload,
                "resolved_identity_fingerprint",
                path,
                line_number,
            ),
            node_identity=_require_str(payload, "node_identity", path, line_number),
            result_artifact=_require_field(payload, "result_artifact", path, line_number),
            terminal_kind=_require_str(payload, "terminal_kind", path, line_number),
        )


@dataclass(frozen=True)
class AgentReplayDecision:
    """Replay identity derived from the current L3 agent invocation."""

    cache_key: str
    resolved_identity_fingerprint: str
    node_identity: str


class AgentJournal:
    """Append-only JSONL journal scoped to a conductor run id."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def for_run(
        cls,
        run_id: str,
        *,
        state_dir: str | Path | None = None,
    ) -> AgentJournal:
        run_dir = _state_dir(state_dir) / "workflows" / run_id
        return cls(run_dir / AGENT_JOURNAL_FILENAME)

    def load_entries(self) -> list[AgentJournalEntry]:
        if not self.path.exists():
            return []

        entries: list[AgentJournalEntry] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line:
                raise JournalCorruptionError(
                    path=self.path,
                    message=f"blank line at {line_number}",
                )
            entries.append(
                AgentJournalEntry.from_json_line(
                    line,
                    path=self.path,
                    line_number=line_number,
                )
            )
        _validate_entry_sequence(entries, self.path)
        return entries

    def latest_generation_entries(self) -> list[AgentJournalEntry]:
        entries = self.load_entries()
        if not entries:
            return []
        latest_generation = max(entry.generation for entry in entries)
        latest_entries = [entry for entry in entries if entry.generation == latest_generation]
        latest_entries.sort(key=lambda entry: entry.entry_index)
        _validate_contiguous_generation(latest_entries, self.path)
        return latest_entries

    def append_entry(self, entry: AgentJournalEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as journal_file:
            journal_file.write(entry.to_json_line())
            journal_file.write("\n")


@dataclass(frozen=True, kw_only=True)
class ProgressJournalEntry:
    """One append-only node-lifecycle progress record (ADR 0002, observational).

    Carries the full identity tuple so the monitor joins progress ↔ agent-journal
    (`node_identity`) ↔ tmux session (`session_id`) without reverse-engineering.
    """

    node_id: str
    node_identity: str
    session_node_key: str
    session_id: str
    attempt: int
    phase: str | None
    status: str
    terminal_kind: str | None
    at: str

    def to_json_line(self) -> str:
        payload: dict[str, Any] = {
            "version": JOURNAL_VERSION,
            "node_id": self.node_id,
            "node_identity": self.node_identity,
            "session_node_key": self.session_node_key,
            "session_id": self.session_id,
            "attempt": self.attempt,
            "phase": self.phase,
            "status": self.status,
            "terminal_kind": self.terminal_kind,
            "at": self.at,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @classmethod
    def from_json_line(cls, line: str) -> ProgressJournalEntry | None:
        """Tolerant parse: a malformed observational line is skipped, never fatal."""
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                return None
            return cls(
                node_id=str(payload["node_id"]),
                node_identity=str(payload.get("node_identity", "")),
                session_node_key=str(payload.get("session_node_key", "")),
                session_id=str(payload.get("session_id", "")),
                attempt=int(payload.get("attempt", 0)),
                phase=(None if payload.get("phase") is None else str(payload["phase"])),
                status=str(payload["status"]),
                terminal_kind=(
                    None if payload.get("terminal_kind") is None else str(payload["terminal_kind"])
                ),
                at=str(payload.get("at", "")),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None


class ProgressJournal:
    """Append-only JSONL observational progress stream for one run (ADR 0002).

    Write-only from the run's perspective — resume/replay never reads it, so its
    presence/absence changes no run outcome. The monitor reads it; malformed
    lines are skipped, never fatal.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def for_run(cls, run_id: str, *, state_dir: str | Path | None = None) -> ProgressJournal:
        run_dir = _state_dir(state_dir) / "workflows" / run_id
        return cls(run_dir / PROGRESS_JOURNAL_FILENAME)

    @classmethod
    def for_run_dir(cls, run_dir: Path) -> ProgressJournal:
        return cls(run_dir / PROGRESS_JOURNAL_FILENAME)

    def load_entries(self) -> list[ProgressJournalEntry]:
        if not self.path.exists():
            return []
        entries: list[ProgressJournalEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            entry = ProgressJournalEntry.from_json_line(line)
            if entry is not None:
                entries.append(entry)
        return entries

    def append_entry(self, entry: ProgressJournalEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as journal_file:
            journal_file.write(entry.to_json_line())
            journal_file.write("\n")

    def latest_by_node(self) -> dict[str, ProgressJournalEntry]:
        """node_id -> latest (file-order last) progress entry."""
        latest: dict[str, ProgressJournalEntry] = {}
        for entry in self.load_entries():
            latest[entry.node_id] = entry
        return latest


@dataclass(frozen=True, kw_only=True)
class GateAnswerJournalEntry:
    """One append-only gate answer journal record (L-K5-1)."""

    gate_id: str
    workflow_id: str
    option: str
    outcome: str
    note: str
    answered_at: str
    gate_reason: str = ""
    gate_stakes: dict[str, Any] = field(default_factory=dict)
    terminal_kind: str = TERMINAL_KIND_GATE_ANSWER

    def to_json_line(self) -> str:
        payload: dict[str, Any] = {
            "version": JOURNAL_VERSION,
            "gate_id": self.gate_id,
            "workflow_id": self.workflow_id,
            "option": self.option,
            "outcome": self.outcome,
            "note": self.note,
            "answered_at": self.answered_at,
            "gate_reason": self.gate_reason,
            "gate_stakes": self.gate_stakes,
            "terminal_kind": self.terminal_kind,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @classmethod
    def from_json_line(cls, line: str, *, path: Path, line_number: int) -> GateAnswerJournalEntry:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise JournalCorruptionError(
                path=path,
                message=f"invalid JSON on line {line_number}: {exc.msg}",
            ) from exc

        if not isinstance(payload, dict):
            raise JournalCorruptionError(
                path=path,
                message=f"line {line_number} is not a JSON object",
            )
        version = _require_int(payload, "version", path, line_number)
        if version != JOURNAL_VERSION:
            raise JournalCorruptionError(
                path=path,
                message=f"line {line_number} has unsupported journal version {version}",
            )

        return cls(
            gate_id=_require_str(payload, "gate_id", path, line_number),
            workflow_id=_require_str(payload, "workflow_id", path, line_number),
            option=_require_str(payload, "option", path, line_number),
            outcome=_require_str(payload, "outcome", path, line_number),
            note=_require_str(payload, "note", path, line_number),
            answered_at=_require_str(payload, "answered_at", path, line_number),
            gate_reason=str(payload.get("gate_reason", "")),
            gate_stakes=_optional_dict(payload, "gate_stakes", path, line_number),
            terminal_kind=_require_str(payload, "terminal_kind", path, line_number),
        )


class GateAnswerJournal:
    """Append-only JSONL journal of gate answers scoped to a conductor run id."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def for_run(
        cls,
        run_id: str,
        *,
        state_dir: str | Path | None = None,
    ) -> GateAnswerJournal:
        run_dir: Path = _state_dir(state_dir) / "workflows" / run_id
        return cls(run_dir / GATE_ANSWER_JOURNAL_FILENAME)

    def load_entries(self) -> list[GateAnswerJournalEntry]:
        if not self.path.exists():
            return []

        entries: list[GateAnswerJournalEntry] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line:
                raise JournalCorruptionError(
                    path=self.path,
                    message=f"blank line at {line_number}",
                )
            entries.append(
                GateAnswerJournalEntry.from_json_line(
                    line,
                    path=self.path,
                    line_number=line_number,
                )
            )
        return entries

    def append_entry(self, entry: GateAnswerJournalEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as journal_file:
            journal_file.write(entry.to_json_line())
            journal_file.write("\n")

    def latest_answers(self) -> dict[str, str]:
        """Return gate_id -> option for the latest answer per gate."""
        entries: list[GateAnswerJournalEntry] = self.load_entries()
        answers: dict[str, str] = {}
        for entry in entries:
            answers[entry.gate_id] = entry.option
        return answers

    def option_counts(self, option: str) -> dict[str, int]:
        """Return gate_id -> count of answers that selected ``option``."""
        counts: dict[str, int] = {}
        for entry in self.load_entries():
            if entry.option != option:
                continue
            counts[entry.gate_id] = counts.get(entry.gate_id, 0) + 1
        return counts

    def latest_gate_stakes(self) -> dict[str, dict[str, Any]]:
        """Return gate_id -> stakes captured when the gate was last answered."""
        stakes: dict[str, dict[str, Any]] = {}
        for entry in self.load_entries():
            stakes[entry.gate_id] = dict(entry.gate_stakes)
        return stakes


@dataclass(frozen=True, kw_only=True)
class CreateWorkspaceJournalEntry:
    """One append-only workspace creation journal record (L-K3-3)."""

    workspace_id: str
    repo: str
    branch: str
    worktree_path: str
    base_ref: str
    issue_id: str | None
    created_at: str
    terminal_kind: str = TERMINAL_KIND_WORKSPACE_CREATED

    def to_json_line(self) -> str:
        payload: dict[str, Any] = {
            "version": JOURNAL_VERSION,
            "workspace_id": self.workspace_id,
            "repo": self.repo,
            "branch": self.branch,
            "worktree_path": self.worktree_path,
            "base_ref": self.base_ref,
            "issue_id": self.issue_id,
            "created_at": self.created_at,
            "terminal_kind": self.terminal_kind,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @classmethod
    def from_json_line(
        cls, line: str, *, path: Path, line_number: int
    ) -> CreateWorkspaceJournalEntry:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise JournalCorruptionError(
                path=path,
                message=f"invalid JSON on line {line_number}: {exc.msg}",
            ) from exc

        if not isinstance(payload, dict):
            raise JournalCorruptionError(
                path=path,
                message=f"line {line_number} is not a JSON object",
            )
        version = _require_int(payload, "version", path, line_number)
        if version != JOURNAL_VERSION:
            raise JournalCorruptionError(
                path=path,
                message=f"line {line_number} has unsupported journal version {version}",
            )

        return cls(
            workspace_id=_require_str(payload, "workspace_id", path, line_number),
            repo=_require_str(payload, "repo", path, line_number),
            branch=_require_str(payload, "branch", path, line_number),
            worktree_path=_require_str(payload, "worktree_path", path, line_number),
            base_ref=_require_str(payload, "base_ref", path, line_number),
            issue_id=_optional_str(payload, "issue_id", path, line_number),
            created_at=_require_str(payload, "created_at", path, line_number),
            terminal_kind=_require_str(payload, "terminal_kind", path, line_number),
        )


class WorkspaceJournal:
    """Append-only JSONL journal of workspace creations scoped to a conductor run id."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def for_run(
        cls,
        run_id: str,
        *,
        state_dir: str | Path | None = None,
    ) -> WorkspaceJournal:
        run_dir: Path = _state_dir(state_dir) / "workflows" / run_id
        return cls(run_dir / WORKSPACE_JOURNAL_FILENAME)

    def load_entries(self) -> list[CreateWorkspaceJournalEntry]:
        if not self.path.exists():
            return []

        entries: list[CreateWorkspaceJournalEntry] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line:
                raise JournalCorruptionError(
                    path=self.path,
                    message=f"blank line at {line_number}",
                )
            entries.append(
                CreateWorkspaceJournalEntry.from_json_line(
                    line,
                    path=self.path,
                    line_number=line_number,
                )
            )
        return entries

    def append_entry(self, entry: CreateWorkspaceJournalEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as journal_file:
            journal_file.write(entry.to_json_line())
            journal_file.write("\n")

    def latest_workspaces(self) -> dict[str, CreateWorkspaceJournalEntry]:
        """Return workspace_id -> entry for the latest entry per workspace (last-wins)."""
        entries: list[CreateWorkspaceJournalEntry] = self.load_entries()
        workspaces: dict[str, CreateWorkspaceJournalEntry] = {}
        for entry in entries:
            workspaces[entry.workspace_id] = entry
        return workspaces


class AgentReplaySession:
    """Stateful replay cursor for one conductor run's journal."""

    def __init__(self, journal: AgentJournal) -> None:
        self.journal = journal
        # ADR 0002: observational progress stream sits beside the agent journal
        # in the same run dir. Constructing it does no I/O; every write is
        # fail-open (see _emit_progress).
        self.progress = ProgressJournal.for_run_dir(journal.path.parent)
        self.previous_entries = journal.latest_generation_entries()
        self.previous_keys = [entry.cache_key for entry in self.previous_entries]
        self.previous_generation = (
            self.previous_entries[0].generation if self.previous_entries else 0
        )
        self.current_generation = self.previous_generation
        self.current_keys: list[str] = []
        self.replayed_prefix_entries: list[AgentJournalEntry] = []
        self.started_new_generation = False

    def run_or_replay(
        self,
        effect: AgentEffect,
        delegate: Callable[[AgentEffect], object],
    ) -> object:
        decision = agent_replay_decision(effect.task)
        self.current_keys.append(decision.cache_key)
        entry_index = len(self.current_keys) - 1
        valid_prefix = longest_valid_prefix(self.previous_keys, self.current_keys)

        if entry_index < valid_prefix:
            previous_entry = self.previous_entries[entry_index]
            if previous_entry.terminal_kind != TERMINAL_KIND_SUCCEEDED:
                self._start_new_generation()
                return self._run_delegate_and_append(effect, delegate, decision, entry_index)
            self._validate_replay_entry(previous_entry, effect, decision)
            self.replayed_prefix_entries.append(previous_entry)
            if self.started_new_generation:
                self._append_replayed_entry(previous_entry)
            # ADR 0002: a resumed cached-prefix node is already done — surface it
            # so the monitor shows DONE on replay (observational; resume itself
            # never reads this).
            self._emit_progress(
                effect.task, decision, PROGRESS_STATUS_SUCCEEDED, terminal_kind=TERMINAL_KIND_SUCCEEDED
            )
            return previous_entry.result_artifact

        if entry_index < len(self.previous_entries):
            self._start_new_generation()

        return self._run_delegate_and_append(effect, delegate, decision, entry_index)

    def _run_delegate_and_append(
        self,
        effect: AgentEffect,
        delegate: Callable[[AgentEffect], object],
        decision: AgentReplayDecision,
        entry_index: int,
    ) -> object:
        # ADR 0002: emit "running" at dispatch from inside the offloaded handler
        # (not before yield Agent in the runtime, which would serialize K4
        # sibling dispatch). Emission is fail-open and never alters the run.
        self._emit_progress(effect.task, decision, PROGRESS_STATUS_RUNNING)
        try:
            result = delegate(effect)
        except AgentAttemptExhaustedError as error:
            self._append_open_gate_entry(
                decision,
                entry_index,
                {
                    "session_id": error.session_id,
                    "attempts": error.attempts,
                    "last_error_kind": error.last_error.kind.value,
                    "last_error_message": error.last_error.message,
                },
            )
            self._emit_progress(
                effect.task, decision, PROGRESS_STATUS_PARKED, terminal_kind=TERMINAL_KIND_OPEN_GATE
            )
            raise
        except AgentDeadlineExceededError as error:
            # L-K4-3: the deadline park is a journaled open-gate terminal,
            # exactly like attempt exhaustion — replay treats it as a
            # non-succeeded entry and re-runs the node (the extension
            # window granted by the gate answer).
            self._append_open_gate_entry(
                decision,
                entry_index,
                {
                    "session_id": error.session_id,
                    "deadline_seconds": error.deadline_seconds,
                    "elapsed_seconds": error.elapsed_seconds,
                    "reason": "wall-clock deadline exceeded",
                },
            )
            self._emit_progress(
                effect.task, decision, PROGRESS_STATUS_PARKED, terminal_kind=TERMINAL_KIND_OPEN_GATE
            )
            raise
        except Exception:
            self._emit_progress(effect.task, decision, PROGRESS_STATUS_FAILED)
            raise

        try:
            _validate_delegate_result(effect, result)
        except Exception:
            self._emit_progress(effect.task, decision, PROGRESS_STATUS_FAILED)
            raise
        self.journal.append_entry(
            AgentJournalEntry(
                generation=self.current_generation,
                entry_index=entry_index,
                cache_key=decision.cache_key,
                resolved_identity_fingerprint=decision.resolved_identity_fingerprint,
                node_identity=decision.node_identity,
                result_artifact=result,
                terminal_kind=TERMINAL_KIND_SUCCEEDED,
            )
        )
        # "succeeded" is emitted only AFTER the validated artifact is journaled,
        # so the progress terminal is artifact-grounded (ADR 0001 D6), never a
        # screen/heuristic judgement.
        self._emit_progress(
            effect.task, decision, PROGRESS_STATUS_SUCCEEDED, terminal_kind=TERMINAL_KIND_SUCCEEDED
        )
        return result

    def _emit_progress(
        self,
        task: AgentTask,
        decision: AgentReplayDecision,
        status: str,
        *,
        terminal_kind: str | None = None,
    ) -> None:
        """ADR 0002: fail-open, non-blocking observational emit (L-K4-1).

        A failed or slow write degrades monitor freshness, NEVER the run — it
        must not raise into the orchestration.
        """
        try:
            self.progress.append_entry(
                ProgressJournalEntry(
                    node_id=task.node_id,
                    node_identity=decision.node_identity,
                    session_node_key=task.session_node_key,
                    session_id=task.session_id,
                    attempt=task.attempt,
                    phase=task.phase,
                    status=status,
                    terminal_kind=terminal_kind,
                    at=datetime.now(timezone.utc).isoformat(),
                )
            )
        except Exception:  # fail-open is the invariant (ADR 0002 D1)
            logger.warning("progress emit failed (run unaffected)", exc_info=True)

    def _append_open_gate_entry(
        self,
        decision: AgentReplayDecision,
        entry_index: int,
        result_artifact: dict[str, Any],
    ) -> None:
        self.journal.append_entry(
            AgentJournalEntry(
                generation=self.current_generation,
                entry_index=entry_index,
                cache_key=decision.cache_key,
                resolved_identity_fingerprint=decision.resolved_identity_fingerprint,
                node_identity=decision.node_identity,
                result_artifact=result_artifact,
                terminal_kind=TERMINAL_KIND_OPEN_GATE,
            )
        )

    def _start_new_generation(self) -> None:
        if self.started_new_generation:
            return
        self.current_generation = self.previous_generation + 1
        for entry_index, previous_entry in enumerate(self.replayed_prefix_entries):
            self._append_replayed_entry(previous_entry, entry_index=entry_index)
        self.started_new_generation = True

    def _append_replayed_entry(
        self,
        previous_entry: AgentJournalEntry,
        *,
        entry_index: int | None = None,
    ) -> None:
        self.journal.append_entry(
            AgentJournalEntry(
                generation=self.current_generation,
                entry_index=previous_entry.entry_index if entry_index is None else entry_index,
                cache_key=previous_entry.cache_key,
                resolved_identity_fingerprint=previous_entry.resolved_identity_fingerprint,
                node_identity=previous_entry.node_identity,
                result_artifact=previous_entry.result_artifact,
                terminal_kind=previous_entry.terminal_kind,
            )
        )

    def _validate_replay_entry(
        self,
        entry: AgentJournalEntry,
        effect: AgentEffect,
        decision: AgentReplayDecision,
    ) -> None:
        if entry.cache_key != decision.cache_key:
            raise JournalCorruptionError(
                path=self.journal.path,
                message=f"entry {entry.entry_index} cache key does not match replay decision",
            )
        if entry.resolved_identity_fingerprint != decision.resolved_identity_fingerprint:
            raise JournalCorruptionError(
                path=self.journal.path,
                message=f"entry {entry.entry_index} identity fingerprint does not match",
            )
        if entry.terminal_kind != TERMINAL_KIND_SUCCEEDED:
            raise JournalCorruptionError(
                path=self.journal.path,
                message=f"entry {entry.entry_index} has unsupported terminal kind",
            )
        validation_error = validate_result_payload(
            entry.result_artifact,
            effect.task.result_schema,
        )
        if validation_error is not None:
            raise JournalCorruptionError(
                path=self.journal.path,
                message=f"entry {entry.entry_index} cached artifact is invalid: {validation_error}",
            )


def agent_replay_decision(task: AgentTask) -> AgentReplayDecision:
    resolved_identity = resolve_agent_task_identity(task)
    identity_fingerprint = resolved_identity_fingerprint(resolved_identity)
    node_path = tuple(part for part in task.node_id.split("/") if part)
    node_identity = node_identity_fingerprint(
        workflow_name=task.run_id,
        node_path=node_path,
        loop_indices=(),
    )
    return AgentReplayDecision(
        cache_key=agent_cache_key(
            prompt=task.prompt,
            schema=task.result_schema,
            resolved_identity=resolved_identity,
        ),
        resolved_identity_fingerprint=identity_fingerprint,
        node_identity=node_identity,
    )


def resolve_agent_task_identity(task: AgentTask) -> ResolvedIdentity:
    if task.resolved_identity is not None:
        return task.resolved_identity
    return ResolvedIdentity(
        adapter=task.agent_type,
        model=task.model or "",
        identity=task.profile,
        effort=task.effort,
    )


def _validate_delegate_result(effect: AgentEffect, result: object) -> None:
    validation_error = validate_result_payload(result, effect.task.result_schema)
    if validation_error is not None:
        raise AgentError(
            agent_id=effect.task.node_id,
            operation="journal",
            message=f"agent handler returned an invalid artifact: {validation_error}",
        )


def _state_dir(state_dir: str | Path | None) -> Path:
    if state_dir is not None:
        return Path(state_dir)
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state is not None:
        return Path(xdg_state) / "doeff-conductor"
    return Path.home() / ".local" / "state" / "doeff-conductor"


def _require_field(
    payload: dict[str, Any],
    field_name: str,
    path: Path,
    line_number: int,
) -> Any:
    if field_name not in payload:
        raise JournalCorruptionError(
            path=path,
            message=f"line {line_number} is missing {field_name!r}",
        )
    return payload[field_name]


def _require_str(
    payload: dict[str, Any],
    field_name: str,
    path: Path,
    line_number: int,
) -> str:
    value = _require_field(payload, field_name, path, line_number)
    if not isinstance(value, str):
        raise JournalCorruptionError(
            path=path,
            message=f"line {line_number} field {field_name!r} is not a string",
        )
    return value


def _optional_str(
    payload: dict[str, Any],
    field_name: str,
    path: Path,
    line_number: int,
) -> str | None:
    value = _require_field(payload, field_name, path, line_number)
    if value is None:
        return None
    if not isinstance(value, str):
        raise JournalCorruptionError(
            path=path,
            message=f"line {line_number} field {field_name!r} is not a string or null",
        )
    return value


def _optional_dict(
    payload: dict[str, Any],
    field_name: str,
    path: Path,
    line_number: int,
) -> dict[str, Any]:
    if field_name not in payload:
        return {}
    value = payload[field_name]
    if not isinstance(value, dict):
        raise JournalCorruptionError(
            path=path,
            message=f"line {line_number} field {field_name!r} is not an object",
        )
    return dict(value)


def _require_int(
    payload: dict[str, Any],
    field_name: str,
    path: Path,
    line_number: int,
) -> int:
    value = _require_field(payload, field_name, path, line_number)
    if not isinstance(value, int) or isinstance(value, bool):
        raise JournalCorruptionError(
            path=path,
            message=f"line {line_number} field {field_name!r} is not an integer",
        )
    if value < 0:
        raise JournalCorruptionError(
            path=path,
            message=f"line {line_number} field {field_name!r} is negative",
        )
    return value


def _validate_entry_sequence(entries: list[AgentJournalEntry], path: Path) -> None:
    seen: set[tuple[int, int]] = set()
    for entry in entries:
        key = (entry.generation, entry.entry_index)
        if key in seen:
            raise JournalCorruptionError(
                path=path,
                message=f"duplicate generation/index entry {key}",
            )
        seen.add(key)


def _validate_contiguous_generation(entries: list[AgentJournalEntry], path: Path) -> None:
    for expected_index, entry in enumerate(entries):
        if entry.entry_index != expected_index:
            raise JournalCorruptionError(
                path=path,
                message=(
                    f"generation {entry.generation} has non-contiguous index "
                    f"{entry.entry_index}; expected {expected_index}"
                ),
            )
