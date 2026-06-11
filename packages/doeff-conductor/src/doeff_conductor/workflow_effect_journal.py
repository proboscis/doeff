"""Durable workflow effect journal for explicit time! and random! replay."""

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from random import SystemRandom
from typing import Any

from doeff_conductor.effects.dsl import RandomCall, TimeCall
from doeff_conductor.exceptions import JournalCorruptionError
from doeff_conductor.replay_keying import longest_valid_prefix, workflow_effect_cache_key

WORKFLOW_EFFECT_JOURNAL_FILENAME = "effect-journal.jsonl"
WORKFLOW_EFFECT_JOURNAL_VERSION = 1
TERMINAL_KIND_SUCCEEDED = "succeeded"
EFFECT_KIND_RANDOM = "random"
EFFECT_KIND_TIME = "time"


@dataclass(frozen=True, kw_only=True)
class WorkflowEffectJournalEntry:
    """One append-only workflow effect journal record."""

    generation: int
    entry_index: int
    cache_key: str
    effect_kind: str
    node_id: str
    value: Any
    terminal_kind: str

    def to_json_line(self) -> str:
        payload: dict[str, Any] = {
            "version": WORKFLOW_EFFECT_JOURNAL_VERSION,
            "generation": self.generation,
            "entry_index": self.entry_index,
            "cache_key": self.cache_key,
            "effect_kind": self.effect_kind,
            "node_id": self.node_id,
            "value": self.value,
            "terminal_kind": self.terminal_kind,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @classmethod
    def from_json_line(
        cls,
        line: str,
        *,
        path: Path,
        line_number: int,
    ) -> "WorkflowEffectJournalEntry":
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
        if version != WORKFLOW_EFFECT_JOURNAL_VERSION:
            raise JournalCorruptionError(
                path=path,
                message=f"line {line_number} has unsupported journal version {version}",
            )

        return cls(
            generation=_require_int(payload, "generation", path, line_number),
            entry_index=_require_int(payload, "entry_index", path, line_number),
            cache_key=_require_str(payload, "cache_key", path, line_number),
            effect_kind=_require_str(payload, "effect_kind", path, line_number),
            node_id=_require_str(payload, "node_id", path, line_number),
            value=_require_field(payload, "value", path, line_number),
            terminal_kind=_require_str(payload, "terminal_kind", path, line_number),
        )


@dataclass(frozen=True)
class WorkflowEffectReplayDecision:
    """Replay identity derived from the current workflow effect invocation."""

    cache_key: str
    effect_kind: str
    node_id: str


class WorkflowEffectJournal:
    """Append-only workflow effect journal scoped to a conductor run id."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def for_run(
        cls,
        run_id: str,
        *,
        state_dir: str | Path | None = None,
    ) -> "WorkflowEffectJournal":
        run_dir = _state_dir(state_dir) / "workflows" / run_id
        return cls(run_dir / WORKFLOW_EFFECT_JOURNAL_FILENAME)

    def load_entries(self) -> list[WorkflowEffectJournalEntry]:
        if not self.path.exists():
            return []

        entries: list[WorkflowEffectJournalEntry] = []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, 1):
            if not line:
                raise JournalCorruptionError(
                    path=self.path,
                    message=f"blank line at {line_number}",
                )
            entries.append(
                WorkflowEffectJournalEntry.from_json_line(
                    line,
                    path=self.path,
                    line_number=line_number,
                )
            )
        _validate_entry_sequence(entries, self.path)
        return entries

    def latest_generation_entries(self) -> list[WorkflowEffectJournalEntry]:
        entries = self.load_entries()
        if not entries:
            return []
        latest_generation = max(entry.generation for entry in entries)
        latest_entries = [entry for entry in entries if entry.generation == latest_generation]
        latest_entries.sort(key=lambda entry: entry.entry_index)
        _validate_contiguous_generation(latest_entries, self.path)
        return latest_entries

    def append_entry(self, entry: WorkflowEffectJournalEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as journal_file:
            journal_file.write(entry.to_json_line())
            journal_file.write("\n")


class WorkflowEffectReplaySession:
    """Stateful replay cursor for one run's workflow effect journal."""

    def __init__(self, journal: WorkflowEffectJournal) -> None:
        self.journal = journal
        self.previous_entries = journal.latest_generation_entries()
        self.previous_keys = [entry.cache_key for entry in self.previous_entries]
        self.previous_generation = (
            self.previous_entries[0].generation if self.previous_entries else 0
        )
        self.current_generation = self.previous_generation
        self.current_keys: list[str] = []
        self.replayed_prefix_entries: list[WorkflowEffectJournalEntry] = []
        self.started_new_generation = False

    def run_or_replay(
        self,
        decision: WorkflowEffectReplayDecision,
        produce_value: Callable[[], Any],
    ) -> Any:
        self.current_keys.append(decision.cache_key)
        entry_index = len(self.current_keys) - 1
        valid_prefix = longest_valid_prefix(self.previous_keys, self.current_keys)

        if entry_index < valid_prefix:
            previous_entry = self.previous_entries[entry_index]
            self._validate_replay_entry(previous_entry, decision)
            self.replayed_prefix_entries.append(previous_entry)
            return previous_entry.value

        if entry_index < len(self.previous_entries):
            self._start_new_generation()

        value = produce_value()
        self.journal.append_entry(
            WorkflowEffectJournalEntry(
                generation=self.current_generation,
                entry_index=entry_index,
                cache_key=decision.cache_key,
                effect_kind=decision.effect_kind,
                node_id=decision.node_id,
                value=value,
                terminal_kind=TERMINAL_KIND_SUCCEEDED,
            )
        )
        return value

    def _start_new_generation(self) -> None:
        if self.started_new_generation:
            return
        self.current_generation = self.previous_generation + 1
        for entry_index, previous_entry in enumerate(self.replayed_prefix_entries):
            self.journal.append_entry(
                WorkflowEffectJournalEntry(
                    generation=self.current_generation,
                    entry_index=entry_index,
                    cache_key=previous_entry.cache_key,
                    effect_kind=previous_entry.effect_kind,
                    node_id=previous_entry.node_id,
                    value=previous_entry.value,
                    terminal_kind=previous_entry.terminal_kind,
                )
            )
        self.started_new_generation = True

    def _validate_replay_entry(
        self,
        entry: WorkflowEffectJournalEntry,
        decision: WorkflowEffectReplayDecision,
    ) -> None:
        if entry.cache_key != decision.cache_key:
            raise JournalCorruptionError(
                path=self.journal.path,
                message=f"entry {entry.entry_index} cache key does not match replay decision",
            )
        if entry.effect_kind != decision.effect_kind:
            raise JournalCorruptionError(
                path=self.journal.path,
                message=f"entry {entry.entry_index} effect kind does not match",
            )
        if entry.node_id != decision.node_id:
            raise JournalCorruptionError(
                path=self.journal.path,
                message=f"entry {entry.entry_index} node id does not match",
            )
        if entry.terminal_kind != TERMINAL_KIND_SUCCEEDED:
            raise JournalCorruptionError(
                path=self.journal.path,
                message=f"entry {entry.entry_index} has unsupported terminal kind",
            )


class JournaledWorkflowEffectHandler:
    """Replay cached workflow effect values before producing new values."""

    def __init__(
        self,
        *,
        state_dir: str | Path | None = None,
        run_id: str | None = None,
    ) -> None:
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self.run_id = run_id
        self._sessions: dict[str, WorkflowEffectReplaySession] = {}

    def handle_time(self, effect: TimeCall) -> str:
        return self._run_or_replay(
            effect,
            lambda: datetime.now(timezone.utc).isoformat(),
        )

    def handle_random(self, effect: RandomCall) -> Any:
        return self._run_or_replay(
            effect,
            lambda: evaluate_random_spec(effect.spec),
        )

    def _run_or_replay(
        self,
        effect: TimeCall | RandomCall,
        produce_value: Callable[[], Any],
    ) -> Any:
        run_id = self._resolve_run_id(effect)
        session = self._sessions.get(run_id)
        if session is None:
            session = WorkflowEffectReplaySession(
                WorkflowEffectJournal.for_run(
                    run_id,
                    state_dir=self.state_dir,
                )
            )
            self._sessions[run_id] = session
        decision = workflow_effect_replay_decision(effect)
        return session.run_or_replay(decision, produce_value)

    def _resolve_run_id(self, effect: TimeCall | RandomCall) -> str:
        if self.run_id is not None:
            return self.run_id
        if effect.run_id is not None:
            return effect.run_id
        raise ValueError("journaled workflow effects require a run_id")


def workflow_effect_replay_decision(
    effect: TimeCall | RandomCall,
) -> WorkflowEffectReplayDecision:
    if isinstance(effect, TimeCall):
        effect_kind = EFFECT_KIND_TIME
        spec: Any = None
    elif isinstance(effect, RandomCall):
        effect_kind = EFFECT_KIND_RANDOM
        spec = effect.spec
    else:
        raise TypeError(f"unsupported workflow effect: {type(effect).__name__}")

    if effect.node_id is None:
        raise ValueError("journaled workflow effects require a node_id")

    return WorkflowEffectReplayDecision(
        cache_key=workflow_effect_cache_key(
            effect_kind=effect_kind,
            node_id=effect.node_id,
            label=effect.label,
            spec=spec,
        ),
        effect_kind=effect_kind,
        node_id=effect.node_id,
    )


def evaluate_random_spec(spec: Any) -> Any:
    random_source = SystemRandom()
    if isinstance(spec, Mapping):
        kind: object | None = spec.get("kind")
        if kind == "choice":
            values: object | None = spec.get("values")
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise TypeError("random! choice requires a non-string sequence of values")
            return random_source.choice(list(values))
    return random_source.random()


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


def _validate_entry_sequence(entries: list[WorkflowEffectJournalEntry], path: Path) -> None:
    seen: set[tuple[int, int]] = set()
    for entry in entries:
        key = (entry.generation, entry.entry_index)
        if key in seen:
            raise JournalCorruptionError(
                path=path,
                message=f"duplicate generation/index entry {key}",
            )
        seen.add(key)


def _validate_contiguous_generation(
    entries: list[WorkflowEffectJournalEntry],
    path: Path,
) -> None:
    for expected_index, entry in enumerate(entries):
        if entry.entry_index != expected_index:
            raise JournalCorruptionError(
                path=path,
                message=(
                    f"generation {entry.generation} has non-contiguous index "
                    f"{entry.entry_index}; expected {expected_index}"
                ),
            )
