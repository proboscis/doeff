"""Journal-materialized overseer progress and gate queue views."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUN_STATE_FILENAME = "run-state.json"
VALID_GATE_OUTCOMES: frozenset[str] = frozenset({"resume", "abort"})


@dataclass(frozen=True)
class GateOption:
    """One closure-preserving gate option."""

    name: str
    outcome: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "outcome": self.outcome,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GateOption:
        outcome: str = str(data["outcome"])
        if outcome not in VALID_GATE_OUTCOMES:
            raise ValueError(
                f"GateOption outcome {outcome!r} not in {sorted(VALID_GATE_OUTCOMES)}"
            )
        return cls(
            name=str(data["name"]),
            outcome=outcome,
            description=str(data["description"]),
        )


@dataclass(frozen=True)
class OpenGateView:
    """Open gate exposed to the overseer queue."""

    gate_id: str
    workflow_id: str
    node_id: str
    phase: str | None
    reason: str
    stakes: dict[str, Any]
    options: tuple[GateOption, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "workflow_id": self.workflow_id,
            "node_id": self.node_id,
            "phase": self.phase,
            "reason": self.reason,
            "stakes": self.stakes,
            "options": [option.to_dict() for option in self.options],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OpenGateView:
        raw_options: object = data.get("options", [])
        if not isinstance(raw_options, list):
            raise ValueError("gate options must be a list")
        options: tuple[GateOption, ...] = tuple(
            GateOption.from_dict(option)
            for option in raw_options
            if isinstance(option, dict)
        )
        return cls(
            gate_id=str(data["gate_id"]),
            workflow_id=str(data["workflow_id"]),
            node_id=str(data["node_id"]),
            phase=data.get("phase") if data.get("phase") is None else str(data.get("phase")),
            reason=str(data["reason"]),
            stakes=dict(data.get("stakes", {})),
            options=options,
        )


@dataclass(frozen=True)
class ProgressEvent:
    """One delta-oriented run progress event."""

    sequence: int
    workflow_id: str
    node_id: str
    phase: str | None
    status: str
    message: str
    terminal_kind: str | None = None
    at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "workflow_id": self.workflow_id,
            "node_id": self.node_id,
            "phase": self.phase,
            "status": self.status,
            "message": self.message,
            "terminal_kind": self.terminal_kind,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgressEvent:
        return cls(
            sequence=int(data["sequence"]),
            workflow_id=str(data["workflow_id"]),
            node_id=str(data["node_id"]),
            phase=data.get("phase") if data.get("phase") is None else str(data.get("phase")),
            status=str(data["status"]),
            message=str(data["message"]),
            terminal_kind=(
                data.get("terminal_kind")
                if data.get("terminal_kind") is None
                else str(data.get("terminal_kind"))
            ),
            at=str(data.get("at", "")),
        )


@dataclass(frozen=True)
class RunStateView:
    """Persisted overseer-visible run state."""

    workflow_id: str
    workflow_name: str
    events: tuple[ProgressEvent, ...]
    open_gates: tuple[OpenGateView, ...]
    supervision: str = "autonomous"
    answered_gates: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "supervision": self.supervision,
            "events": [event.to_dict() for event in self.events],
            "open_gates": [gate.to_dict() for gate in self.open_gates],
            "answered_gates": dict(self.answered_gates),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunStateView:
        raw_events: object = data.get("events", [])
        raw_gates: object = data.get("open_gates", [])
        if not isinstance(raw_events, list):
            raise ValueError("run-state events must be a list")
        if not isinstance(raw_gates, list):
            raise ValueError("run-state open_gates must be a list")
        raw_answered: object = data.get("answered_gates", {})
        if not isinstance(raw_answered, dict):
            raise ValueError("run-state answered_gates must be an object")
        events: tuple[ProgressEvent, ...] = tuple(
            ProgressEvent.from_dict(event)
            for event in raw_events
            if isinstance(event, dict)
        )
        gates: tuple[OpenGateView, ...] = tuple(
            OpenGateView.from_dict(gate)
            for gate in raw_gates
            if isinstance(gate, dict)
        )
        return cls(
            workflow_id=str(data["workflow_id"]),
            workflow_name=str(data["workflow_name"]),
            supervision=str(data.get("supervision", "autonomous")),
            events=events,
            open_gates=gates,
            answered_gates={str(key): str(value) for key, value in raw_answered.items()},
        )


def run_state_path(state_dir: str | Path, workflow_id: str) -> Path:
    return Path(state_dir) / "workflows" / workflow_id / RUN_STATE_FILENAME


def save_run_state(state_dir: str | Path, run_state: RunStateView) -> None:
    path: Path = run_state_path(state_dir, run_state.workflow_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(run_state.to_dict(), indent=2, sort_keys=True, ensure_ascii=True),
        encoding="utf-8",
    )


def load_run_state(state_dir: str | Path, workflow_id: str) -> RunStateView:
    path: Path = run_state_path(state_dir, workflow_id)
    if not path.exists():
        raise FileNotFoundError(f"run state not found for workflow {workflow_id!r}")
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("run state payload must be an object")
    return RunStateView.from_dict(payload)


def progress_since(state_dir: str | Path, workflow_id: str, since_sequence: int) -> list[dict[str, Any]]:
    run_state: RunStateView = load_run_state(state_dir, workflow_id)
    return [
        event.to_dict()
        for event in run_state.events
        if event.sequence > since_sequence
    ]


def list_open_gates(state_dir: str | Path, workflow_id: str | None = None) -> list[dict[str, Any]]:
    base_dir: Path = Path(state_dir) / "workflows"
    if workflow_id is not None:
        try:
            run_state = load_run_state(state_dir, workflow_id)
        except FileNotFoundError:
            return []
        return [gate.to_dict() for gate in run_state.open_gates]

    gates: list[dict[str, Any]] = []
    if not base_dir.exists():
        return gates
    for workflow_dir in sorted(base_dir.iterdir()):
        if not workflow_dir.is_dir():
            continue
        path: Path = workflow_dir / RUN_STATE_FILENAME
        if not path.exists():
            continue
        run_state: RunStateView = load_run_state(state_dir, workflow_dir.name)
        gates.extend(gate.to_dict() for gate in run_state.open_gates)
    return gates


def list_gates_with_status(
    state_dir: str | Path,
    workflow_id: str,
) -> list[dict[str, Any]]:
    """Return both open and answered gates with a STATUS field.

    Open gates get status='open'; answered gates get status='answered'
    with the chosen option included.
    """
    try:
        run_state: RunStateView = load_run_state(state_dir, workflow_id)
    except FileNotFoundError:
        return []

    gates: list[dict[str, Any]] = []
    for gate in run_state.open_gates:
        gate_dict: dict[str, Any] = gate.to_dict()
        gate_dict["status"] = "open"
        gates.append(gate_dict)

    answered: dict[str, str] = answered_gate_options(state_dir, workflow_id)
    for gate_id, option in answered.items():
        gates.append({
            "gate_id": gate_id,
            "workflow_id": workflow_id,
            "status": "answered",
            "option": option,
        })

    return gates


def answered_gate_options(state_dir: str | Path, workflow_id: str) -> dict[str, str]:
    """Return gate_id -> option for answered gates.

    Reads from the gate answer journal as the authoritative source (L-K5-2),
    falling back to run_state.answered_gates for backward compatibility with
    pre-journal runs.
    """
    from doeff_conductor.journal import GateAnswerJournal

    gate_answer_journal: GateAnswerJournal = GateAnswerJournal.for_run(
        workflow_id,
        state_dir=state_dir,
    )
    journal_answers: dict[str, str] = gate_answer_journal.latest_answers()
    if journal_answers:
        return journal_answers

    try:
        run_state: RunStateView = load_run_state(state_dir, workflow_id)
    except FileNotFoundError:
        return {}
    return dict(run_state.answered_gates)


def record_open_gates(
    state_dir: str | Path,
    *,
    workflow_id: str,
    workflow_name: str,
    open_gates: tuple[OpenGateView, ...],
    supervision: str,
) -> RunStateView:
    try:
        existing: RunStateView = load_run_state(state_dir, workflow_id)
    except FileNotFoundError:
        existing = RunStateView(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            events=(),
            open_gates=(),
            supervision=supervision,
        )

    events: list[ProgressEvent] = list(existing.events)
    gates_by_id: dict[str, OpenGateView] = {
        gate.gate_id: gate for gate in existing.open_gates
    }
    sequence = _last_sequence(existing.events)
    for gate in open_gates:
        if gate.gate_id not in gates_by_id:
            sequence += 1
            events.append(
                make_progress_event(
                    sequence=sequence,
                    workflow_id=workflow_id,
                    node_id=gate.node_id,
                    phase=gate.phase,
                    status="open",
                    message=f"{gate.reason}: {gate.gate_id}",
                    terminal_kind="gate",
                )
            )
        gates_by_id[gate.gate_id] = gate

    updated = RunStateView(
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        events=tuple(events),
        open_gates=tuple(gates_by_id.values()),
        supervision=supervision,
        answered_gates=dict(existing.answered_gates),
    )
    save_run_state(state_dir, updated)
    return updated


def record_gate_answer(
    state_dir: str | Path,
    *,
    workflow_id: str,
    gate_id: str,
    option: str,
    note: str = "",
) -> RunStateView:
    from doeff_conductor.journal import GateAnswerJournal, GateAnswerJournalEntry

    existing: RunStateView = load_run_state(state_dir, workflow_id)
    target_gate: OpenGateView | None = None
    remaining_gates: list[OpenGateView] = []
    for gate in existing.open_gates:
        if gate.gate_id == gate_id:
            target_gate = gate
        else:
            remaining_gates.append(gate)
    if target_gate is None:
        raise ValueError(f"open gate not found: {gate_id}")

    valid_options = {gate_option.name for gate_option in target_gate.options}
    if option not in valid_options:
        raise ValueError(f"gate {gate_id!r} does not support option {option!r}")

    selected_gate_option: GateOption = _find_gate_option(target_gate, option)

    gate_answer_journal: GateAnswerJournal = GateAnswerJournal.for_run(
        workflow_id,
        state_dir=state_dir,
    )
    gate_answer_journal.append_entry(
        GateAnswerJournalEntry(
            gate_id=gate_id,
            workflow_id=workflow_id,
            option=option,
            outcome=selected_gate_option.outcome,
            note=note,
            answered_at=datetime.now(timezone.utc).isoformat(),
        )
    )

    sequence = _last_sequence(existing.events) + 1
    events = (
        *existing.events,
        make_progress_event(
            sequence=sequence,
            workflow_id=workflow_id,
            node_id=target_gate.node_id,
            phase=target_gate.phase,
            status="answered",
            message=f"{gate_id} answered with {option}",
            terminal_kind="gate-answer",
        ),
    )
    answered_gates = dict(existing.answered_gates)
    answered_gates[gate_id] = option
    updated = RunStateView(
        workflow_id=workflow_id,
        workflow_name=existing.workflow_name,
        events=events,
        open_gates=tuple(remaining_gates),
        supervision=existing.supervision,
        answered_gates=answered_gates,
    )
    save_run_state(state_dir, updated)
    return updated


def _find_gate_option(gate: OpenGateView, option_name: str) -> GateOption:
    for gate_option in gate.options:
        if gate_option.name == option_name:
            return gate_option
    raise ValueError(f"gate {gate.gate_id!r} does not have option {option_name!r}")


def make_progress_event(
    *,
    sequence: int,
    workflow_id: str,
    node_id: str,
    phase: str | None,
    status: str,
    message: str,
    terminal_kind: str | None = None,
) -> ProgressEvent:
    return ProgressEvent(
        sequence=sequence,
        workflow_id=workflow_id,
        node_id=node_id,
        phase=phase,
        status=status,
        message=message,
        terminal_kind=terminal_kind,
        at=datetime.now(timezone.utc).isoformat(),
    )


def _last_sequence(events: tuple[ProgressEvent, ...]) -> int:
    if not events:
        return 0
    return max(event.sequence for event in events)
