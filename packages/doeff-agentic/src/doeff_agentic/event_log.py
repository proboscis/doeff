"""
JSONL event log system for doeff-agentic.

This module provides the event logging infrastructure for workflow observability.

Directory structure:
    ~/.local/state/doeff-agentic/workflows/
    ├── a3f8b2c/
    │   ├── workflow.jsonl      # Workflow-level events
    │   ├── sessions/
    │   │   ├── reviewer.jsonl  # Per-session events
    │   │   ├── fixer.jsonl
    │   │   └── tester.jsonl
    │   └── environments/
    │       ├── env-abc.jsonl
    │       └── env-def.jsonl
    └── b7e1d4f/
        └── ...

Event format (JSONL lines):
    {"ts": "2026-01-03T10:00:00Z", "event_type": "workflow.created", ...}
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

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


class WorkflowEventType(str, Enum):
    """Workflow-level event types."""

    CREATED = "workflow.created"
    STATUS = "workflow.status"
    METADATA = "workflow.metadata"
    COMPLETED = "workflow.completed"
    ERROR = "workflow.error"
    ABORTED = "workflow.aborted"


class SessionEventType(str, Enum):
    """Session-level event types."""

    CREATED = "session.created"
    STATUS = "session.status"
    FORKED = "session.forked"
    ABORTED = "session.aborted"
    DELETED = "session.deleted"


class MessageEventType(str, Enum):
    """Message-level event types."""

    SENT = "message.sent"
    STARTED = "message.started"
    CHUNK = "message.chunk"
    COMPLETE = "message.complete"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"


class EnvironmentEventType(str, Enum):
    """Environment-level event types."""

    CREATED = "environment.created"
    DELETED = "environment.deleted"


# =============================================================================
# Event Data Classes
# =============================================================================


@dataclass
class LogEvent:
    """Base class for log events."""

    ts: datetime
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "ts": self.ts.isoformat(),
            "event_type": self.event_type,
        }
        result.update(self.data)
        return result

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LogEvent:
        """Create from dictionary."""
        ts = datetime.fromisoformat(d["ts"])
        event_type = d["event_type"]
        data = {k: v for k, v in d.items() if k not in ("ts", "event_type")}
        return cls(ts=ts, event_type=event_type, data=data)


# =============================================================================
# Event Log Writer
# =============================================================================


def _atomic_append(path: Path, content: str) -> None:
    """Atomically append content to a file.

    Uses atomic write to a temp file, then append to handle concurrent access.
    For JSONL files, each line is independent so concurrent appends are safe.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # For JSONL, direct append is safe as each line is complete
    with path.open("a") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


def get_default_event_log_dir() -> Path:
    """Get the default event log directory following XDG Base Directory Specification.

    Returns:
        Path to ~/.local/state/doeff-agentic/workflows/
    """
    xdg_state = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    return Path(xdg_state) / "doeff-agentic" / "workflows"


@dataclass
class EventLogWriter:
    """Writer for JSONL event logs.

    Manages writing events to the appropriate log files for a workflow.
    """

    workflow_id: str
    base_dir: Path

    def __init__(
        self,
        workflow_id: str,
        base_dir: Path | str | None = None,
    ) -> None:
        """Initialize the event log writer.

        Args:
            workflow_id: Workflow identifier
            base_dir: Base directory for event logs (defaults to XDG state dir)
        """
        self.workflow_id = workflow_id
        if base_dir is None:
            self.base_dir = get_default_event_log_dir()
        else:
            self.base_dir = Path(base_dir)

    @property
    def workflow_dir(self) -> Path:
        """Get the workflow directory."""
        return self.base_dir / self.workflow_id

    @property
    def workflow_log_path(self) -> Path:
        """Get the workflow log file path."""
        return self.workflow_dir / "workflow.jsonl"

    def session_log_path(self, session_name: str) -> Path:
        """Get the session log file path."""
        return self.workflow_dir / "sessions" / f"{session_name}.jsonl"

    def environment_log_path(self, environment_id: str) -> Path:
        """Get the environment log file path."""
        return self.workflow_dir / "environments" / f"{environment_id}.jsonl"

    def _write_event(self, path: Path, event: LogEvent) -> None:
        """Write an event to a log file."""
        line = json.dumps(event.to_dict()) + "\n"
        _atomic_append(path, line)

    # -------------------------------------------------------------------------
    # Workflow Events
    # -------------------------------------------------------------------------

    def log_workflow_created(
        self,
        handle: AgenticWorkflowHandle,
    ) -> None:
        """Log workflow creation event."""
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=WorkflowEventType.CREATED.value,
            data={
                "id": handle.id,
                "name": handle.name,
                "status": handle.status.value,
                "metadata": handle.metadata,
            },
        )
        self._write_event(self.workflow_log_path, event)

    def log_workflow_status(
        self,
        status: AgenticWorkflowStatus,
        message: str | None = None,
    ) -> None:
        """Log workflow status change."""
        data: dict[str, Any] = {"status": status.value}
        if message:
            data["message"] = message
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=WorkflowEventType.STATUS.value,
            data=data,
        )
        self._write_event(self.workflow_log_path, event)

    def log_workflow_completed(self, result: Any = None) -> None:
        """Log workflow completion."""
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=WorkflowEventType.COMPLETED.value,
            data={"result": str(result) if result is not None else None},
        )
        self._write_event(self.workflow_log_path, event)

    def log_workflow_error(self, error: str) -> None:
        """Log workflow error."""
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=WorkflowEventType.ERROR.value,
            data={"error": error},
        )
        self._write_event(self.workflow_log_path, event)

    # -------------------------------------------------------------------------
    # Session Events
    # -------------------------------------------------------------------------

    def log_session_created(
        self,
        handle: AgenticSessionHandle,
    ) -> None:
        """Log session creation event."""
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=SessionEventType.CREATED.value,
            data={
                "id": handle.id,
                "name": handle.name,
                "environment_id": handle.environment_id,
                "status": handle.status.value,
                "title": handle.title,
                "agent": handle.agent,
                "model": handle.model,
            },
        )
        # Write to both workflow log and session log
        self._write_event(self.workflow_log_path, event)
        self._write_event(self.session_log_path(handle.name), event)

    def log_session_status(
        self,
        session_name: str,
        status: AgenticSessionStatus,
    ) -> None:
        """Log session status change."""
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=SessionEventType.STATUS.value,
            data={
                "name": session_name,
                "status": status.value,
            },
        )
        self._write_event(self.workflow_log_path, event)
        self._write_event(self.session_log_path(session_name), event)

    def log_session_forked(
        self,
        handle: AgenticSessionHandle,
        source_session_id: str,
        message_id: str | None = None,
    ) -> None:
        """Log session fork event."""
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=SessionEventType.FORKED.value,
            data={
                "id": handle.id,
                "name": handle.name,
                "source_session_id": source_session_id,
                "message_id": message_id,
                "environment_id": handle.environment_id,
            },
        )
        self._write_event(self.workflow_log_path, event)
        self._write_event(self.session_log_path(handle.name), event)

    def log_session_aborted(self, session_name: str, session_id: str) -> None:
        """Log session abort event."""
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=SessionEventType.ABORTED.value,
            data={"name": session_name, "id": session_id},
        )
        self._write_event(self.workflow_log_path, event)
        self._write_event(self.session_log_path(session_name), event)

    def log_session_deleted(self, session_name: str, session_id: str) -> None:
        """Log session deletion event."""
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=SessionEventType.DELETED.value,
            data={"name": session_name, "id": session_id},
        )
        self._write_event(self.workflow_log_path, event)

    # -------------------------------------------------------------------------
    # Message Events
    # -------------------------------------------------------------------------

    def log_message_sent(
        self,
        session_name: str,
        message_id: str,
        role: str,
        content_preview: str,
    ) -> None:
        """Log message sent event."""
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=MessageEventType.SENT.value,
            data={
                "id": message_id,
                "role": role,
                "preview": content_preview[:200],  # Limit preview length
            },
        )
        self._write_event(self.session_log_path(session_name), event)

    def log_message_chunk(
        self,
        session_name: str,
        content: str,
    ) -> None:
        """Log message chunk event."""
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=MessageEventType.CHUNK.value,
            data={"content": content[:500]},  # Limit chunk size
        )
        self._write_event(self.session_log_path(session_name), event)

    def log_message_complete(
        self,
        session_name: str,
        message_id: str,
        tokens: int | None = None,
    ) -> None:
        """Log message completion event."""
        data: dict[str, Any] = {"id": message_id}
        if tokens is not None:
            data["tokens"] = tokens
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=MessageEventType.COMPLETE.value,
            data=data,
        )
        self._write_event(self.session_log_path(session_name), event)

    def log_tool_call(
        self,
        session_name: str,
        tool: str,
        args: dict[str, Any],
    ) -> None:
        """Log tool call event."""
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=MessageEventType.TOOL_CALL.value,
            data={"tool": tool, "args": args},
        )
        self._write_event(self.session_log_path(session_name), event)

    def log_tool_result(
        self,
        session_name: str,
        tool: str,
        result_preview: str,
    ) -> None:
        """Log tool result event."""
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=MessageEventType.TOOL_RESULT.value,
            data={"tool": tool, "result": result_preview[:500]},
        )
        self._write_event(self.session_log_path(session_name), event)

    # -------------------------------------------------------------------------
    # Environment Events
    # -------------------------------------------------------------------------

    def log_environment_created(
        self,
        handle: AgenticEnvironmentHandle,
    ) -> None:
        """Log environment creation event."""
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=EnvironmentEventType.CREATED.value,
            data={
                "id": handle.id,
                "env_type": handle.env_type.value,
                "name": handle.name,
                "working_dir": handle.working_dir,
                "base_commit": handle.base_commit,
                "source_environment_id": handle.source_environment_id,
            },
        )
        self._write_event(self.workflow_log_path, event)
        self._write_event(self.environment_log_path(handle.id), event)

    def log_environment_deleted(self, environment_id: str) -> None:
        """Log environment deletion event."""
        event = LogEvent(
            ts=datetime.now(timezone.utc),
            event_type=EnvironmentEventType.DELETED.value,
            data={"id": environment_id},
        )
        self._write_event(self.workflow_log_path, event)


# =============================================================================
# Event Log Reader
# =============================================================================


def read_log_file(path: Path) -> Iterator[LogEvent]:
    """Read events from a JSONL log file.

    Args:
        path: Path to the JSONL file

    Yields:
        LogEvent objects
    """
    if not path.exists():
        return

    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    data = json.loads(line)
                    yield LogEvent.from_dict(data)
                except json.JSONDecodeError:
                    continue  # Skip malformed lines


@dataclass
class WorkflowState:
    """Reconstructed workflow state from event logs."""

    id: str
    name: str | None = None
    status: AgenticWorkflowStatus = AgenticWorkflowStatus.PENDING
    created_at: datetime | None = None
    metadata: dict[str, Any] | None = None
    error: str | None = None
    sessions: dict[str, AgenticSessionHandle] = field(default_factory=dict)
    environments: dict[str, AgenticEnvironmentHandle] = field(default_factory=dict)

    def to_handle(self) -> AgenticWorkflowHandle:
        """Convert to AgenticWorkflowHandle."""
        return AgenticWorkflowHandle(
            id=self.id,
            name=self.name,
            status=self.status,
            created_at=self.created_at or datetime.now(timezone.utc),
            metadata=self.metadata,
        )


class EventLogReader:
    """Reader for reconstructing state from JSONL event logs."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        """Initialize the event log reader.

        Args:
            base_dir: Base directory for event logs (defaults to XDG state dir)
        """
        if base_dir is None:
            self.base_dir = get_default_event_log_dir()
        else:
            self.base_dir = Path(base_dir)

    def list_workflows(self) -> list[str]:
        """List all workflow IDs."""
        if not self.base_dir.exists():
            return []

        workflows = []
        for item in self.base_dir.iterdir():
            if item.is_dir() and (item / "workflow.jsonl").exists():
                workflows.append(item.name)
        return sorted(workflows)

    def resolve_prefix(self, prefix: str) -> str | None:
        """Resolve a workflow ID prefix to full ID.

        Args:
            prefix: Full or prefix workflow ID (min 3 chars)

        Returns:
            Full workflow ID if unique match found, None otherwise

        Raises:
            ValueError: If prefix matches multiple workflows
        """
        workflows = self.list_workflows()

        # Exact match
        if prefix in workflows:
            return prefix

        # Prefix match
        matches = [wf for wf in workflows if wf.startswith(prefix)]

        if len(matches) == 0:
            return None
        elif len(matches) == 1:
            return matches[0]
        else:
            raise ValueError(f"Ambiguous prefix '{prefix}' matches: {matches}")

    def reconstruct_workflow(self, workflow_id: str) -> WorkflowState | None:
        """Reconstruct workflow state from event log.

        Args:
            workflow_id: Full or prefix workflow ID

        Returns:
            WorkflowState if found, None otherwise
        """
        # Resolve prefix
        try:
            full_id = self.resolve_prefix(workflow_id)
        except ValueError:
            return None

        if full_id is None:
            return None

        workflow_dir = self.base_dir / full_id
        workflow_log = workflow_dir / "workflow.jsonl"

        if not workflow_log.exists():
            return None

        state = WorkflowState(id=full_id)

        # Process events in order
        for event in read_log_file(workflow_log):
            self._apply_event(state, event)

        return state

    def _apply_event(self, state: WorkflowState, event: LogEvent) -> None:
        """Apply an event to reconstruct state."""
        event_type = event.event_type

        if event_type == WorkflowEventType.CREATED.value:
            state.name = event.data.get("name")
            state.status = AgenticWorkflowStatus(event.data.get("status", "pending"))
            state.created_at = event.ts
            state.metadata = event.data.get("metadata")

        elif event_type == WorkflowEventType.STATUS.value:
            state.status = AgenticWorkflowStatus(event.data.get("status", "running"))

        elif event_type == WorkflowEventType.COMPLETED.value:
            state.status = AgenticWorkflowStatus.DONE

        elif event_type == WorkflowEventType.ERROR.value:
            state.status = AgenticWorkflowStatus.ERROR
            state.error = event.data.get("error")

        elif event_type == WorkflowEventType.ABORTED.value:
            state.status = AgenticWorkflowStatus.ABORTED

        elif event_type == SessionEventType.CREATED.value:
            session = AgenticSessionHandle(
                id=event.data.get("id", ""),
                name=event.data.get("name", ""),
                workflow_id=state.id,
                environment_id=event.data.get("environment_id", ""),
                status=AgenticSessionStatus(event.data.get("status", "pending")),
                created_at=event.ts,
                title=event.data.get("title"),
                agent=event.data.get("agent"),
                model=event.data.get("model"),
            )
            state.sessions[session.name] = session

        elif event_type == SessionEventType.STATUS.value:
            name = event.data.get("name")
            if name and name in state.sessions:
                old = state.sessions[name]
                state.sessions[name] = AgenticSessionHandle(
                    id=old.id,
                    name=old.name,
                    workflow_id=old.workflow_id,
                    environment_id=old.environment_id,
                    status=AgenticSessionStatus(event.data.get("status", "running")),
                    created_at=old.created_at,
                    title=old.title,
                    agent=old.agent,
                    model=old.model,
                )

        elif event_type == SessionEventType.FORKED.value:
            session = AgenticSessionHandle(
                id=event.data.get("id", ""),
                name=event.data.get("name", ""),
                workflow_id=state.id,
                environment_id=event.data.get("environment_id", ""),
                status=AgenticSessionStatus.PENDING,
                created_at=event.ts,
            )
            state.sessions[session.name] = session

        elif event_type == SessionEventType.ABORTED.value:
            name = event.data.get("name")
            if name and name in state.sessions:
                old = state.sessions[name]
                state.sessions[name] = AgenticSessionHandle(
                    id=old.id,
                    name=old.name,
                    workflow_id=old.workflow_id,
                    environment_id=old.environment_id,
                    status=AgenticSessionStatus.ABORTED,
                    created_at=old.created_at,
                    title=old.title,
                    agent=old.agent,
                    model=old.model,
                )

        elif event_type == SessionEventType.DELETED.value:
            name = event.data.get("name")
            if name and name in state.sessions:
                del state.sessions[name]

        elif event_type == EnvironmentEventType.CREATED.value:
            env = AgenticEnvironmentHandle(
                id=event.data.get("id", ""),
                env_type=AgenticEnvironmentType(event.data.get("env_type", "shared")),
                name=event.data.get("name"),
                working_dir=event.data.get("working_dir", ""),
                created_at=event.ts,
                base_commit=event.data.get("base_commit"),
                source_environment_id=event.data.get("source_environment_id"),
            )
            state.environments[env.id] = env

        elif event_type == EnvironmentEventType.DELETED.value:
            env_id = event.data.get("id")
            if env_id and env_id in state.environments:
                del state.environments[env_id]

    def get_session_events(
        self,
        workflow_id: str,
        session_name: str,
    ) -> list[LogEvent]:
        """Get all events for a specific session.

        Args:
            workflow_id: Workflow identifier
            session_name: Session name

        Returns:
            List of events in chronological order
        """
        full_id = self.resolve_prefix(workflow_id)
        if full_id is None:
            return []

        session_log = self.base_dir / full_id / "sessions" / f"{session_name}.jsonl"
        return list(read_log_file(session_log))

    def get_workflow_events(self, workflow_id: str) -> list[LogEvent]:
        """Get all workflow-level events.

        Args:
            workflow_id: Workflow identifier

        Returns:
            List of events in chronological order
        """
        full_id = self.resolve_prefix(workflow_id)
        if full_id is None:
            return []

        workflow_log = self.base_dir / full_id / "workflow.jsonl"
        return list(read_log_file(workflow_log))


__all__ = [
    # Event types
    "WorkflowEventType",
    "SessionEventType",
    "MessageEventType",
    "EnvironmentEventType",
    # Events
    "LogEvent",
    # Writer
    "EventLogWriter",
    "get_default_event_log_dir",
    # Reader
    "EventLogReader",
    "WorkflowState",
    "read_log_file",
]
