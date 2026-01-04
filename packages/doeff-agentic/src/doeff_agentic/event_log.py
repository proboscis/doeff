"""
JSONL Event Log System for doeff-agentic.

This module implements the event logging system as specified in SPEC-AGENTIC-001.

Event log structure:
    ~/.local/state/doeff-agentic/workflows/
    ├── a3f8b2c/
    │   ├── workflow.jsonl      # Workflow-level events
    │   ├── sessions/
    │   │   ├── reviewer.jsonl  # Per-session events
    │   │   └── fixer.jsonl
    │   └── environments/
    │       ├── env-abc.jsonl
    │       └── env-def.jsonl

Event Types:
    Workflow Events (workflow.jsonl):
        - workflow.created: Workflow instance created
        - workflow.status: Workflow status changed
        - environment.created: Environment created in workflow
        - environment.deleted: Environment deleted
        - session.created: Session created in workflow
        - session.status: Session status changed
        - session.deleted: Session deleted

    Session Events (sessions/<name>.jsonl):
        - message.sent: User message sent
        - message.started: Assistant started responding
        - message.chunk: Partial content received
        - message.complete: Full response received
        - tool.call: Tool invocation started
        - tool.result: Tool returned result

    Environment Events (environments/<id>.jsonl):
        - environment.created: Environment created
        - environment.session_bound: Session bound to environment
        - environment.session_unbound: Session unbound from environment
        - environment.deleted: Environment deleted
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .types import (
    AgenticEnvironmentHandle,
    AgenticEnvironmentType,
    AgenticSessionHandle,
    AgenticSessionStatus,
    AgenticWorkflowHandle,
    AgenticWorkflowStatus,
)


def get_default_state_dir() -> Path:
    """Get the default state directory following XDG Base Directory Specification."""
    xdg_state = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    return Path(xdg_state) / "doeff-agentic"


@dataclass
class EventLogEntry:
    """A single event log entry."""

    ts: str  # ISO 8601 timestamp
    event_type: str  # e.g., "workflow.created", "session.status"
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "ts": self.ts,
            "event_type": self.event_type,
            **self.data,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventLogEntry:
        """Create from dictionary."""
        ts = data.pop("ts")
        event_type = data.pop("event_type")
        return cls(ts=ts, event_type=event_type, data=data)


@dataclass
class EventLogWriter:
    """Writer for JSONL event logs.

    Manages the event log files for a workflow, including:
    - workflow.jsonl for workflow-level events
    - sessions/<name>.jsonl for per-session events
    - environments/<id>.jsonl for environment events
    """

    workflow_id: str
    state_dir: Path = field(default_factory=get_default_state_dir)

    def __post_init__(self) -> None:
        """Ensure directory structure exists."""
        self._ensure_dirs()

    @property
    def workflow_dir(self) -> Path:
        """Get the workflow directory path."""
        return self.state_dir / "workflows" / self.workflow_id

    def _ensure_dirs(self) -> None:
        """Ensure all required directories exist."""
        (self.workflow_dir / "sessions").mkdir(parents=True, exist_ok=True)
        (self.workflow_dir / "environments").mkdir(parents=True, exist_ok=True)

    def _append(self, path: Path, entry: EventLogEntry) -> None:
        """Append an entry to a log file."""
        with path.open("a") as f:
            f.write(entry.to_json() + "\n")

    def _now(self) -> str:
        """Get current timestamp in ISO 8601 format."""
        return datetime.now(timezone.utc).isoformat()

    # -------------------------------------------------------------------------
    # Workflow Events
    # -------------------------------------------------------------------------

    def log_workflow_created(
        self,
        workflow: AgenticWorkflowHandle,
    ) -> None:
        """Log workflow.created event."""
        entry = EventLogEntry(
            ts=self._now(),
            event_type="workflow.created",
            data={
                "id": workflow.id,
                "name": workflow.name,
                "status": workflow.status.value,
                "metadata": workflow.metadata,
            },
        )
        self._append(self.workflow_dir / "workflow.jsonl", entry)

    def log_workflow_status(
        self,
        status: AgenticWorkflowStatus,
        error: str | None = None,
    ) -> None:
        """Log workflow.status event."""
        data: dict[str, Any] = {"status": status.value}
        if error:
            data["error"] = error
        entry = EventLogEntry(ts=self._now(), event_type="workflow.status", data=data)
        self._append(self.workflow_dir / "workflow.jsonl", entry)

    # -------------------------------------------------------------------------
    # Session Events (workflow.jsonl)
    # -------------------------------------------------------------------------

    def log_session_created(
        self,
        session: AgenticSessionHandle,
    ) -> None:
        """Log session.created event to workflow log."""
        entry = EventLogEntry(
            ts=self._now(),
            event_type="session.created",
            data={
                "id": session.id,
                "name": session.name,
                "environment_id": session.environment_id,
                "title": session.title,
                "agent": session.agent,
                "model": session.model,
            },
        )
        self._append(self.workflow_dir / "workflow.jsonl", entry)

    def log_session_status(
        self,
        session_name: str,
        status: AgenticSessionStatus,
        error: str | None = None,
    ) -> None:
        """Log session.status event to workflow log."""
        data: dict[str, Any] = {"name": session_name, "status": status.value}
        if error:
            data["error"] = error
        entry = EventLogEntry(ts=self._now(), event_type="session.status", data=data)
        self._append(self.workflow_dir / "workflow.jsonl", entry)

    def log_session_deleted(
        self,
        session_name: str,
    ) -> None:
        """Log session.deleted event to workflow log."""
        entry = EventLogEntry(
            ts=self._now(),
            event_type="session.deleted",
            data={"name": session_name},
        )
        self._append(self.workflow_dir / "workflow.jsonl", entry)

    # -------------------------------------------------------------------------
    # Environment Events (workflow.jsonl)
    # -------------------------------------------------------------------------

    def log_environment_created(
        self,
        env: AgenticEnvironmentHandle,
    ) -> None:
        """Log environment.created event to workflow log."""
        entry = EventLogEntry(
            ts=self._now(),
            event_type="environment.created",
            data={
                "id": env.id,
                "env_type": env.env_type.value,
                "name": env.name,
                "working_dir": env.working_dir,
                "base_commit": env.base_commit,
                "source_environment_id": env.source_environment_id,
            },
        )
        self._append(self.workflow_dir / "workflow.jsonl", entry)
        # Also log to environment-specific file
        self._log_environment_event(env.id, entry)

    def log_environment_deleted(
        self,
        environment_id: str,
    ) -> None:
        """Log environment.deleted event to workflow log."""
        entry = EventLogEntry(
            ts=self._now(),
            event_type="environment.deleted",
            data={"id": environment_id},
        )
        self._append(self.workflow_dir / "workflow.jsonl", entry)
        self._log_environment_event(environment_id, entry)

    # -------------------------------------------------------------------------
    # Session-Specific Events (sessions/<name>.jsonl)
    # -------------------------------------------------------------------------

    def log_message_sent(
        self,
        session_name: str,
        content: str,
        message_id: str | None = None,
    ) -> None:
        """Log message.sent event to session log."""
        entry = EventLogEntry(
            ts=self._now(),
            event_type="message.sent",
            data={
                "role": "user",
                "preview": content[:100] + ("..." if len(content) > 100 else ""),
                "message_id": message_id,
            },
        )
        self._append(self.workflow_dir / "sessions" / f"{session_name}.jsonl", entry)

    def log_message_chunk(
        self,
        session_name: str,
        content: str,
    ) -> None:
        """Log message.chunk event to session log."""
        entry = EventLogEntry(
            ts=self._now(),
            event_type="message.chunk",
            data={"content": content},
        )
        self._append(self.workflow_dir / "sessions" / f"{session_name}.jsonl", entry)

    def log_message_complete(
        self,
        session_name: str,
        message_id: str | None = None,
        tokens: int | None = None,
    ) -> None:
        """Log message.complete event to session log."""
        data: dict[str, Any] = {}
        if message_id:
            data["message_id"] = message_id
        if tokens:
            data["tokens"] = tokens
        entry = EventLogEntry(ts=self._now(), event_type="message.complete", data=data)
        self._append(self.workflow_dir / "sessions" / f"{session_name}.jsonl", entry)

    def log_tool_call(
        self,
        session_name: str,
        tool: str,
        args: dict[str, Any],
    ) -> None:
        """Log tool.call event to session log."""
        entry = EventLogEntry(
            ts=self._now(),
            event_type="tool.call",
            data={"tool": tool, "args": args},
        )
        self._append(self.workflow_dir / "sessions" / f"{session_name}.jsonl", entry)

    def log_tool_result(
        self,
        session_name: str,
        tool: str,
        result: Any,
    ) -> None:
        """Log tool.result event to session log."""
        entry = EventLogEntry(
            ts=self._now(),
            event_type="tool.result",
            data={"tool": tool, "result": result},
        )
        self._append(self.workflow_dir / "sessions" / f"{session_name}.jsonl", entry)

    def log_session_event(
        self,
        session_name: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Log a generic event to session log."""
        entry = EventLogEntry(ts=self._now(), event_type=event_type, data=data)
        self._append(self.workflow_dir / "sessions" / f"{session_name}.jsonl", entry)

    # -------------------------------------------------------------------------
    # Environment-Specific Events (environments/<id>.jsonl)
    # -------------------------------------------------------------------------

    def _log_environment_event(
        self,
        environment_id: str,
        entry: EventLogEntry,
    ) -> None:
        """Log an event to environment-specific log file."""
        self._append(self.workflow_dir / "environments" / f"{environment_id}.jsonl", entry)

    def log_environment_session_bound(
        self,
        environment_id: str,
        session_name: str,
    ) -> None:
        """Log environment.session_bound event."""
        entry = EventLogEntry(
            ts=self._now(),
            event_type="environment.session_bound",
            data={"session_name": session_name},
        )
        self._log_environment_event(environment_id, entry)

    def log_environment_session_unbound(
        self,
        environment_id: str,
        session_name: str,
    ) -> None:
        """Log environment.session_unbound event."""
        entry = EventLogEntry(
            ts=self._now(),
            event_type="environment.session_unbound",
            data={"session_name": session_name},
        )
        self._log_environment_event(environment_id, entry)

    # -------------------------------------------------------------------------
    # Reading Events
    # -------------------------------------------------------------------------

    def read_workflow_events(self) -> list[EventLogEntry]:
        """Read all workflow events."""
        return self._read_events(self.workflow_dir / "workflow.jsonl")

    def read_session_events(self, session_name: str) -> list[EventLogEntry]:
        """Read all events for a session."""
        return self._read_events(self.workflow_dir / "sessions" / f"{session_name}.jsonl")

    def read_environment_events(self, environment_id: str) -> list[EventLogEntry]:
        """Read all events for an environment."""
        return self._read_events(self.workflow_dir / "environments" / f"{environment_id}.jsonl")

    def _read_events(self, path: Path) -> list[EventLogEntry]:
        """Read events from a log file."""
        if not path.exists():
            return []

        entries: list[EventLogEntry] = []
        with path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    entries.append(EventLogEntry.from_dict(data))
        return entries

    def list_sessions(self) -> list[str]:
        """List all session names with log files."""
        sessions_dir = self.workflow_dir / "sessions"
        if not sessions_dir.exists():
            return []
        return [p.stem for p in sessions_dir.glob("*.jsonl")]

    def list_environments(self) -> list[str]:
        """List all environment IDs with log files."""
        envs_dir = self.workflow_dir / "environments"
        if not envs_dir.exists():
            return []
        return [p.stem for p in envs_dir.glob("*.jsonl")]


@dataclass
class EventLogReader:
    """Reader for JSONL event logs.

    Provides utilities for reading and reconstructing state from event logs.
    """

    state_dir: Path = field(default_factory=get_default_state_dir)

    def list_workflows(self) -> list[str]:
        """List all workflow IDs with event logs."""
        workflows_dir = self.state_dir / "workflows"
        if not workflows_dir.exists():
            return []
        return [
            d.name
            for d in workflows_dir.iterdir()
            if d.is_dir() and (d / "workflow.jsonl").exists()
        ]

    def get_writer(self, workflow_id: str) -> EventLogWriter:
        """Get a writer for a workflow."""
        return EventLogWriter(workflow_id=workflow_id, state_dir=self.state_dir)

    def read_workflow_events(self, workflow_id: str) -> list[EventLogEntry]:
        """Read all workflow events."""
        return self.get_writer(workflow_id).read_workflow_events()

    def read_session_events(self, workflow_id: str, session_name: str) -> list[EventLogEntry]:
        """Read all events for a session."""
        return self.get_writer(workflow_id).read_session_events(session_name)

    def read_environment_events(self, workflow_id: str, environment_id: str) -> list[EventLogEntry]:
        """Read all events for an environment."""
        return self.get_writer(workflow_id).read_environment_events(environment_id)

    def reconstruct_workflow_state(self, workflow_id: str) -> dict[str, Any] | None:
        """Reconstruct workflow state from event log.

        Returns a dictionary with:
        - id: Workflow ID
        - name: Workflow name
        - status: Current status
        - sessions: dict of session_name -> {status, environment_id, ...}
        - environments: dict of env_id -> {env_type, working_dir, ...}
        """
        events = self.read_workflow_events(workflow_id)
        if not events:
            return None

        state: dict[str, Any] = {
            "id": workflow_id,
            "name": None,
            "status": "pending",
            "sessions": {},
            "environments": {},
            "error": None,
        }

        for event in events:
            if event.event_type == "workflow.created":
                state["name"] = event.data.get("name")
                state["status"] = event.data.get("status", "running")
            elif event.event_type == "workflow.status":
                state["status"] = event.data.get("status")
                state["error"] = event.data.get("error")
            elif event.event_type == "session.created":
                name = event.data.get("name")
                if name:
                    state["sessions"][name] = {
                        "id": event.data.get("id"),
                        "status": "pending",
                        "environment_id": event.data.get("environment_id"),
                        "title": event.data.get("title"),
                        "agent": event.data.get("agent"),
                        "model": event.data.get("model"),
                    }
            elif event.event_type == "session.status":
                name = event.data.get("name")
                if name and name in state["sessions"]:
                    state["sessions"][name]["status"] = event.data.get("status")
                    if event.data.get("error"):
                        state["sessions"][name]["error"] = event.data["error"]
            elif event.event_type == "session.deleted":
                name = event.data.get("name")
                if name and name in state["sessions"]:
                    del state["sessions"][name]
            elif event.event_type == "environment.created":
                env_id = event.data.get("id")
                if env_id:
                    state["environments"][env_id] = {
                        "env_type": event.data.get("env_type"),
                        "name": event.data.get("name"),
                        "working_dir": event.data.get("working_dir"),
                        "base_commit": event.data.get("base_commit"),
                        "source_environment_id": event.data.get("source_environment_id"),
                    }
            elif event.event_type == "environment.deleted":
                env_id = event.data.get("id")
                if env_id and env_id in state["environments"]:
                    del state["environments"][env_id]

        return state


__all__ = [
    "EventLogEntry",
    "EventLogWriter",
    "EventLogReader",
    "get_default_state_dir",
]
