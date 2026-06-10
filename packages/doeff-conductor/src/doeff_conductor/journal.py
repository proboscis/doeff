"""Durable L3 agent effect journal for conductor workflow replay."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doeff_agents.result_validation import validate_result_payload

from doeff_conductor.effects.agent import AgentEffect, AgentTask
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
TERMINAL_KIND_SUCCEEDED = "succeeded"


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


class AgentReplaySession:
    """Stateful replay cursor for one conductor run's journal."""

    def __init__(self, journal: AgentJournal) -> None:
        self.journal = journal
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
            self._validate_replay_entry(previous_entry, effect, decision)
            self.replayed_prefix_entries.append(previous_entry)
            return previous_entry.result_artifact

        if entry_index < len(self.previous_entries):
            self._start_new_generation()

        result = delegate(effect)
        _validate_delegate_result(effect, result)
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
        return result

    def _start_new_generation(self) -> None:
        if self.started_new_generation:
            return
        self.current_generation = self.previous_generation + 1
        for entry_index, previous_entry in enumerate(self.replayed_prefix_entries):
            self.journal.append_entry(
                AgentJournalEntry(
                    generation=self.current_generation,
                    entry_index=entry_index,
                    cache_key=previous_entry.cache_key,
                    resolved_identity_fingerprint=previous_entry.resolved_identity_fingerprint,
                    node_identity=previous_entry.node_identity,
                    result_artifact=previous_entry.result_artifact,
                    terminal_kind=previous_entry.terminal_kind,
                )
            )
        self.started_new_generation = True

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
