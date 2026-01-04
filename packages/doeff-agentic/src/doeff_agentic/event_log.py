"""
JSONL event log management for doeff-agentic.

This module provides event-sourced state management using JSONL files:
- Each workflow has an event log file (workflow.jsonl)
- Each session has its own event log (sessions/<name>.jsonl)
- Each environment has its own event log (environments/<id>.jsonl)

Events are append-only, and state is reconstructed by replaying them.

Directory structure:
    ~/.local/state/doeff-agentic/workflows/
    ├── a3f8b2c/
    │   ├── workflow.jsonl          # Workflow-level events
    │   ├── sessions/
    │   │   ├── reviewer.jsonl      # Per-session events
    │   │   ├── fixer.jsonl
    │   │   └── tester.jsonl
    │   └── environments/
    │       ├── env-abc.jsonl
    │       └── env-def.jsonl
    └── b7e1d4f/
        └── ...
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterator
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

# =============================================================================
# Event Types
# =============================================================================


@dataclass
class WorkflowEvent:
    """Event in a workflow's event log."""

    ts: datetime
    event_type: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "event_type": self.event_type,
            **self.data,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkflowEvent:
        ts = datetime.fromisoformat(d["ts"])
        event_type = d["event_type"]
        data = {k: v for k, v in d.items() if k not in ("ts", "event_type")}
        return cls(ts=ts, event_type=event_type, data=data)


@dataclass
class SessionEvent:
    """Event in a session's event log."""

    ts: datetime
    event_type: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "event_type": self.event_type,
            **self.data,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionEvent:
        ts = datetime.fromisoformat(d["ts"])
        event_type = d["event_type"]
        data = {k: v for k, v in d.items() if k not in ("ts", "event_type")}
        return cls(ts=ts, event_type=event_type, data=data)


# =============================================================================
# Reconstructed State
# =============================================================================


@dataclass
class WorkflowState:
    """Reconstructed workflow state from event log."""

    id: str
    name: str | None
    status: AgenticWorkflowStatus
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] | None = None
    environments: dict[str, AgenticEnvironmentHandle] = field(default_factory=dict)
    sessions: dict[str, AgenticSessionHandle] = field(default_factory=dict)
    error: str | None = None

    def to_workflow_handle(self) -> AgenticWorkflowHandle:
        """Convert to AgenticWorkflowHandle."""
        return AgenticWorkflowHandle(
            id=self.id,
            name=self.name,
            status=self.status,
            created_at=self.created_at,
            metadata=self.metadata,
        )


# =============================================================================
# Helper Functions
# =============================================================================


def _atomic_append(path: Path, content: str) -> None:
    """Append content to file atomically (best effort)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(content + "\n")


def get_default_state_dir() -> Path:
    """Get the default state directory following XDG specification."""
    xdg_state = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    return Path(xdg_state) / "doeff-agentic"


def generate_workflow_id(name: str | None = None) -> str:
    """Generate a 7-char hex workflow ID."""
    data = f"{name or 'workflow'}-{time.time()}"
    return hashlib.sha256(data.encode()).hexdigest()[:7]


# =============================================================================
# Event Log Manager
# =============================================================================


class EventLogManager:
    """Manages JSONL event logs for workflows, sessions, and environments.

    This class provides:
    - Event writing (append-only JSONL)
    - State reconstruction from events
    - Query methods for workflows/sessions/environments
    """

    def __init__(self, state_dir: Path | str | None = None) -> None:
        """Initialize the event log manager.

        Args:
            state_dir: Root directory for event logs (defaults to XDG state dir)
        """
        if state_dir is None:
            self.state_dir = get_default_state_dir()
        else:
            self.state_dir = Path(state_dir)

    def _workflow_dir(self, workflow_id: str) -> Path:
        """Get the directory for a workflow."""
        return self.state_dir / "workflows" / workflow_id

    def _workflow_log_path(self, workflow_id: str) -> Path:
        """Get path to workflow event log."""
        return self._workflow_dir(workflow_id) / "workflow.jsonl"

    def _session_log_path(self, workflow_id: str, session_name: str) -> Path:
        """Get path to session event log."""
        return self._workflow_dir(workflow_id) / "sessions" / f"{session_name}.jsonl"

    def _environment_log_path(self, workflow_id: str, env_id: str) -> Path:
        """Get path to environment event log."""
        return self._workflow_dir(workflow_id) / "environments" / f"{env_id}.jsonl"

    # -------------------------------------------------------------------------
    # Event Writing
    # -------------------------------------------------------------------------

    def write_workflow_event(
        self,
        workflow_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Write an event to the workflow log.

        Args:
            workflow_id: Workflow identifier
            event_type: Event type (e.g., "workflow.created", "session.status")
            data: Additional event data
        """
        event = WorkflowEvent(
            ts=datetime.now(timezone.utc),
            event_type=event_type,
            data=data or {},
        )
        path = self._workflow_log_path(workflow_id)
        _atomic_append(path, json.dumps(event.to_dict()))

    def write_session_event(
        self,
        workflow_id: str,
        session_name: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Write an event to a session log.

        Args:
            workflow_id: Workflow identifier
            session_name: Session name
            event_type: Event type (e.g., "message.sent", "message.complete")
            data: Additional event data
        """
        event = SessionEvent(
            ts=datetime.now(timezone.utc),
            event_type=event_type,
            data=data or {},
        )
        path = self._session_log_path(workflow_id, session_name)
        _atomic_append(path, json.dumps(event.to_dict()))

    def write_environment_event(
        self,
        workflow_id: str,
        env_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Write an event to an environment log.

        Args:
            workflow_id: Workflow identifier
            env_id: Environment identifier
            event_type: Event type (e.g., "environment.created")
            data: Additional event data
        """
        event = WorkflowEvent(
            ts=datetime.now(timezone.utc),
            event_type=event_type,
            data=data or {},
        )
        path = self._environment_log_path(workflow_id, env_id)
        _atomic_append(path, json.dumps(event.to_dict()))

    # -------------------------------------------------------------------------
    # Workflow Event Helpers
    # -------------------------------------------------------------------------

    def log_workflow_created(
        self,
        workflow_id: str,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log workflow creation event."""
        self.write_workflow_event(
            workflow_id,
            "workflow.created",
            {"id": workflow_id, "name": name, "metadata": metadata},
        )

    def log_workflow_status(
        self,
        workflow_id: str,
        status: AgenticWorkflowStatus,
        error: str | None = None,
    ) -> None:
        """Log workflow status change event."""
        data: dict[str, Any] = {"status": status.value}
        if error:
            data["error"] = error
        self.write_workflow_event(workflow_id, "workflow.status", data)

    def log_environment_created(
        self,
        workflow_id: str,
        env: AgenticEnvironmentHandle,
    ) -> None:
        """Log environment creation event."""
        self.write_workflow_event(
            workflow_id,
            "environment.created",
            {
                "id": env.id,
                "env_type": env.env_type.value,
                "name": env.name,
                "working_dir": env.working_dir,
                "base_commit": env.base_commit,
                "source_environment_id": env.source_environment_id,
            },
        )
        # Also write to environment-specific log
        self.write_environment_event(
            workflow_id,
            env.id,
            "created",
            {"working_dir": env.working_dir},
        )

    def log_environment_deleted(
        self,
        workflow_id: str,
        env_id: str,
    ) -> None:
        """Log environment deletion event."""
        self.write_workflow_event(
            workflow_id,
            "environment.deleted",
            {"id": env_id},
        )

    def log_session_created(
        self,
        workflow_id: str,
        session: AgenticSessionHandle,
    ) -> None:
        """Log session creation event."""
        self.write_workflow_event(
            workflow_id,
            "session.created",
            {
                "id": session.id,
                "name": session.name,
                "environment_id": session.environment_id,
                "title": session.title,
                "agent": session.agent,
                "model": session.model,
            },
        )
        # Also write to session-specific log
        self.write_session_event(
            workflow_id,
            session.name,
            "created",
            {"session_id": session.id, "environment_id": session.environment_id},
        )

    def log_session_status(
        self,
        workflow_id: str,
        session_name: str,
        status: AgenticSessionStatus,
    ) -> None:
        """Log session status change event."""
        self.write_workflow_event(
            workflow_id,
            "session.status",
            {"name": session_name, "status": status.value},
        )
        self.write_session_event(
            workflow_id,
            session_name,
            "status",
            {"status": status.value},
        )

    def log_session_deleted(
        self,
        workflow_id: str,
        session_name: str,
    ) -> None:
        """Log session deletion event."""
        self.write_workflow_event(
            workflow_id,
            "session.deleted",
            {"name": session_name},
        )

    def log_message_sent(
        self,
        workflow_id: str,
        session_name: str,
        role: str,
        content_preview: str,
    ) -> None:
        """Log message sent event."""
        self.write_session_event(
            workflow_id,
            session_name,
            "message.sent",
            {"role": role, "preview": content_preview[:100]},
        )

    def log_message_complete(
        self,
        workflow_id: str,
        session_name: str,
        token_count: int | None = None,
    ) -> None:
        """Log message complete event."""
        data: dict[str, Any] = {}
        if token_count is not None:
            data["tokens"] = token_count
        self.write_session_event(
            workflow_id,
            session_name,
            "message.complete",
            data,
        )

    # -------------------------------------------------------------------------
    # State Reconstruction
    # -------------------------------------------------------------------------

    def read_workflow_events(self, workflow_id: str) -> list[WorkflowEvent]:
        """Read all events from a workflow log.

        Args:
            workflow_id: Workflow identifier

        Returns:
            List of events in chronological order
        """
        path = self._workflow_log_path(workflow_id)
        if not path.exists():
            return []

        events = []
        with path.open() as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    try:
                        events.append(WorkflowEvent.from_dict(json.loads(stripped)))
                    except (json.JSONDecodeError, KeyError):
                        continue
        return events

    def read_session_events(
        self, workflow_id: str, session_name: str
    ) -> list[SessionEvent]:
        """Read all events from a session log.

        Args:
            workflow_id: Workflow identifier
            session_name: Session name

        Returns:
            List of events in chronological order
        """
        path = self._session_log_path(workflow_id, session_name)
        if not path.exists():
            return []

        events = []
        with path.open() as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    try:
                        events.append(SessionEvent.from_dict(json.loads(stripped)))
                    except (json.JSONDecodeError, KeyError):
                        continue
        return events

    def reconstruct_workflow_state(self, workflow_id: str) -> WorkflowState | None:
        """Reconstruct workflow state from event log.

        Args:
            workflow_id: Workflow identifier

        Returns:
            Reconstructed WorkflowState or None if not found
        """
        events = self.read_workflow_events(workflow_id)
        if not events:
            return None

        # Initialize state
        state = WorkflowState(
            id=workflow_id,
            name=None,
            status=AgenticWorkflowStatus.PENDING,
            created_at=events[0].ts,
            updated_at=events[0].ts,
        )

        # Replay events
        for event in events:
            state.updated_at = event.ts

            if event.event_type == "workflow.created":
                state.name = event.data.get("name")
                state.metadata = event.data.get("metadata")
                state.status = AgenticWorkflowStatus.RUNNING

            elif event.event_type == "workflow.status":
                status_str = event.data.get("status", "pending")
                state.status = AgenticWorkflowStatus(status_str)
                state.error = event.data.get("error")

            elif event.event_type == "environment.created":
                env_id = event.data["id"]
                state.environments[env_id] = AgenticEnvironmentHandle(
                    id=env_id,
                    env_type=AgenticEnvironmentType(event.data["env_type"]),
                    name=event.data.get("name"),
                    working_dir=event.data["working_dir"],
                    created_at=event.ts,
                    base_commit=event.data.get("base_commit"),
                    source_environment_id=event.data.get("source_environment_id"),
                )

            elif event.event_type == "environment.deleted":
                env_id = event.data["id"]
                state.environments.pop(env_id, None)

            elif event.event_type == "session.created":
                name = event.data["name"]
                state.sessions[name] = AgenticSessionHandle(
                    id=event.data["id"],
                    name=name,
                    workflow_id=workflow_id,
                    environment_id=event.data["environment_id"],
                    status=AgenticSessionStatus.PENDING,
                    created_at=event.ts,
                    title=event.data.get("title"),
                    agent=event.data.get("agent"),
                    model=event.data.get("model"),
                )

            elif event.event_type == "session.status":
                name = event.data["name"]
                if name in state.sessions:
                    old = state.sessions[name]
                    state.sessions[name] = AgenticSessionHandle(
                        id=old.id,
                        name=old.name,
                        workflow_id=old.workflow_id,
                        environment_id=old.environment_id,
                        status=AgenticSessionStatus(event.data["status"]),
                        created_at=old.created_at,
                        title=old.title,
                        agent=old.agent,
                        model=old.model,
                    )

            elif event.event_type == "session.deleted":
                name = event.data["name"]
                state.sessions.pop(name, None)

        return state

    def get_workflow_handle(self, workflow_id: str) -> AgenticWorkflowHandle | None:
        """Get workflow handle by reconstructing from events.

        Args:
            workflow_id: Workflow identifier

        Returns:
            AgenticWorkflowHandle or None if not found
        """
        state = self.reconstruct_workflow_state(workflow_id)
        if state is None:
            return None
        return state.to_workflow_handle()

    # -------------------------------------------------------------------------
    # Query Methods
    # -------------------------------------------------------------------------

    def list_workflow_ids(self) -> list[str]:
        """List all workflow IDs.

        Returns:
            List of workflow IDs
        """
        workflows_dir = self.state_dir / "workflows"
        if not workflows_dir.exists():
            return []

        return [
            d.name
            for d in workflows_dir.iterdir()
            if d.is_dir() and (d / "workflow.jsonl").exists()
        ]

    def list_workflows(
        self,
        status: list[AgenticWorkflowStatus] | None = None,
    ) -> list[AgenticWorkflowHandle]:
        """List workflows with optional filtering.

        Args:
            status: Filter by workflow status

        Returns:
            List of AgenticWorkflowHandle sorted by updated_at descending
        """
        handles = []
        for wf_id in self.list_workflow_ids():
            state = self.reconstruct_workflow_state(wf_id)
            if state is None:
                continue

            # Apply status filter
            if status and state.status not in status:
                continue

            handles.append(state.to_workflow_handle())

        # Sort by created_at descending
        handles.sort(key=lambda h: h.created_at, reverse=True)
        return handles

    def resolve_prefix(self, prefix: str) -> str | None:
        """Resolve a workflow ID prefix to full ID.

        Args:
            prefix: Full or prefix workflow ID (min 3 chars)

        Returns:
            Full workflow ID if unique match, None if not found

        Raises:
            ValueError: If prefix matches multiple workflows
        """
        all_ids = self.list_workflow_ids()

        # Exact match
        if prefix in all_ids:
            return prefix

        # Prefix match
        matches = [wf_id for wf_id in all_ids if wf_id.startswith(prefix)]

        if len(matches) == 0:
            return None
        if len(matches) == 1:
            return matches[0]
        raise ValueError(f"Ambiguous prefix '{prefix}' matches: {matches}")

    def delete_workflow(self, workflow_id: str) -> bool:
        """Delete a workflow and all its event logs.

        Args:
            workflow_id: Full or prefix workflow ID

        Returns:
            True if deleted, False if not found
        """
        import shutil

        full_id = self.resolve_prefix(workflow_id)
        if full_id is None:
            return False

        workflow_dir = self._workflow_dir(full_id)
        if workflow_dir.exists():
            shutil.rmtree(workflow_dir)
            return True
        return False

    def watch_workflow(
        self,
        workflow_id: str,
        poll_interval: float = 1.0,
    ) -> Iterator[WorkflowState]:
        """Watch a workflow for changes by polling event log.

        Args:
            workflow_id: Full or prefix workflow ID
            poll_interval: Seconds between polls

        Yields:
            WorkflowState on each change
        """
        import time

        full_id = self.resolve_prefix(workflow_id)
        if full_id is None:
            return

        last_event_count = 0
        last_updated = None

        while True:
            state = self.reconstruct_workflow_state(full_id)
            if state is None:
                return

            events = self.read_workflow_events(full_id)
            event_count = len(events)

            # Yield if new events
            if event_count > last_event_count or last_updated != state.updated_at:
                last_event_count = event_count
                last_updated = state.updated_at
                yield state

            # Check for terminal status
            if state.status.is_terminal():
                return

            time.sleep(poll_interval)


__all__ = [
    "EventLogManager",
    "SessionEvent",
    "WorkflowEvent",
    "WorkflowState",
    "generate_workflow_id",
    "get_default_state_dir",
]
