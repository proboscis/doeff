"""Journal-materialized overseer progress and gate queue views."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUN_STATE_FILENAME = "run-state.json"


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
        return cls(
            name=str(data["name"]),
            outcome=str(data["outcome"]),
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "supervision": self.supervision,
            "events": [event.to_dict() for event in self.events],
            "open_gates": [gate.to_dict() for gate in self.open_gates],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunStateView:
        raw_events: object = data.get("events", [])
        raw_gates: object = data.get("open_gates", [])
        if not isinstance(raw_events, list):
            raise ValueError("run-state events must be a list")
        if not isinstance(raw_gates, list):
            raise ValueError("run-state open_gates must be a list")
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
        return [gate.to_dict() for gate in load_run_state(state_dir, workflow_id).open_gates]

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
