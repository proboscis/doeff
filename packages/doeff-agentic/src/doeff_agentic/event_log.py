"""
JSONL Event Log Management for doeff-agentic.

This module implements the event-sourced state management as specified in
SPEC-AGENTIC-001-opencode-session-redesign.md.

State directory structure:
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

Event Types:
    Workflow Events (workflow.jsonl):
    - workflow.created: Workflow created
    - workflow.status: Status change
    - environment.created: Environment created in workflow
    - environment.deleted: Environment deleted
    - session.created: Session created in workflow
    - session.status: Session status change

    Session Events (sessions/<name>.jsonl):
    - message.sent: User message sent
    - message.started: Assistant started responding
    - message.chunk: Partial content received
    - message.complete: Full response received
    - tool.call: Tool invocation started
    - tool.result: Tool returned result
    - session.blocked: Session waiting for user input
    - session.error: Session encountered error
    - session.done: Session completed

    Environment Events (environments/<id>.jsonl):
    - environment.created: Environment created
    - environment.deleted: Environment deleted
    - session.bound: Session bound to environment
    - session.unbound: Session unbound from environment
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
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


def _atomic_append(path: Path, content: str) -> None:
    """Append content to file atomically.

    For JSONL, we just append directly with file locking if available.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)
        if not content.endswith("\n"):
            f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def _atomic_write(path: Path, content: str) -> None:
    """Write content to file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode())
        os.close(fd)
        os.rename(temp_path, path)
    except Exception:
        os.close(fd)
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _now_iso() -> str:
    """Get current timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


# =============================================================================
# Event Types
# =============================================================================


@dataclass(frozen=True)
class EventLogEntry:
    """A single event log entry."""

    ts: str  # ISO timestamp
    event_type: str
    data: dict[str, Any]

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps({"ts": self.ts, "event_type": self.event_type, **self.data})

    @classmethod
    def from_json(cls, line: str) -> EventLogEntry:
        """Parse from JSON string."""
        obj = json.loads(line)
        ts = obj.pop("ts")
        event_type = obj.pop("event_type")
        return cls(ts=ts, event_type=event_type, data=obj)


# =============================================================================
# Event Log Writer
# =============================================================================


class EventLogWriter:
    """Writer for JSONL event logs.

    Provides methods to log events for workflows, sessions, and environments.
    Each event is appended to the appropriate JSONL file.
    """

    def __init__(self, state_dir: Path | str | None = None) -> None:
        """Initialize the event log writer.

        Args:
            state_dir: Directory for state files (defaults to XDG state dir)
        """
        if state_dir is None:
            xdg_state = os.environ.get(
                "XDG_STATE_HOME", str(Path.home() / ".local" / "state")
            )
            self.state_dir = Path(xdg_state) / "doeff-agentic"
        else:
            self.state_dir = Path(state_dir)

    def _workflow_dir(self, workflow_id: str) -> Path:
        """Get workflow directory path."""
        return self.state_dir / "workflows" / workflow_id

    def _workflow_log_path(self, workflow_id: str) -> Path:
        """Get workflow event log path."""
        return self._workflow_dir(workflow_id) / "workflow.jsonl"

    def _session_log_path(self, workflow_id: str, session_name: str) -> Path:
        """Get session event log path."""
        return self._workflow_dir(workflow_id) / "sessions" / f"{session_name}.jsonl"

    def _environment_log_path(self, workflow_id: str, env_id: str) -> Path:
        """Get environment event log path."""
        return self._workflow_dir(workflow_id) / "environments" / f"{env_id}.jsonl"

    def _append_workflow_event(
        self, workflow_id: str, event_type: str, **data: Any
    ) -> None:
        """Append event to workflow log."""
        entry = EventLogEntry(ts=_now_iso(), event_type=event_type, data=data)
        _atomic_append(self._workflow_log_path(workflow_id), entry.to_json())

    def _append_session_event(
        self, workflow_id: str, session_name: str, event_type: str, **data: Any
    ) -> None:
        """Append event to session log."""
        entry = EventLogEntry(ts=_now_iso(), event_type=event_type, data=data)
        _atomic_append(
            self._session_log_path(workflow_id, session_name), entry.to_json()
        )

    def _append_environment_event(
        self, workflow_id: str, env_id: str, event_type: str, **data: Any
    ) -> None:
        """Append event to environment log."""
        entry = EventLogEntry(ts=_now_iso(), event_type=event_type, data=data)
        _atomic_append(
            self._environment_log_path(workflow_id, env_id), entry.to_json()
        )

    # -------------------------------------------------------------------------
    # Workflow Events
    # -------------------------------------------------------------------------

    def log_workflow_created(
        self, workflow_id: str, name: str | None = None, metadata: dict | None = None
    ) -> None:
        """Log workflow creation event."""
        self._append_workflow_event(
            workflow_id,
            "workflow.created",
            id=workflow_id,
            name=name,
            metadata=metadata,
        )

    def log_workflow_status(
        self, workflow_id: str, status: AgenticWorkflowStatus | str
    ) -> None:
        """Log workflow status change event."""
        status_str = status.value if isinstance(status, AgenticWorkflowStatus) else status
        self._append_workflow_event(
            workflow_id,
            "workflow.status",
            status=status_str,
        )

    # -------------------------------------------------------------------------
    # Environment Events
    # -------------------------------------------------------------------------

    def log_environment_created(
        self, workflow_id: str, env: AgenticEnvironmentHandle
    ) -> None:
        """Log environment creation event."""
        # Log to workflow
        self._append_workflow_event(
            workflow_id,
            "environment.created",
            id=env.id,
            env_type=env.env_type.value,
            name=env.name,
            working_dir=env.working_dir,
            base_commit=env.base_commit,
            source_environment_id=env.source_environment_id,
        )
        # Log to environment file
        self._append_environment_event(
            workflow_id,
            env.id,
            "environment.created",
            id=env.id,
            env_type=env.env_type.value,
            name=env.name,
            working_dir=env.working_dir,
            base_commit=env.base_commit,
            source_environment_id=env.source_environment_id,
        )

    def log_environment_deleted(
        self, workflow_id: str, env_id: str, force: bool = False
    ) -> None:
        """Log environment deletion event."""
        self._append_workflow_event(
            workflow_id,
            "environment.deleted",
            id=env_id,
            force=force,
        )
        self._append_environment_event(
            workflow_id,
            env_id,
            "environment.deleted",
            force=force,
        )

    def log_session_bound_to_environment(
        self, workflow_id: str, env_id: str, session_name: str
    ) -> None:
        """Log session binding to environment."""
        self._append_environment_event(
            workflow_id,
            env_id,
            "session.bound",
            session_name=session_name,
        )

    def log_session_unbound_from_environment(
        self, workflow_id: str, env_id: str, session_name: str
    ) -> None:
        """Log session unbinding from environment."""
        self._append_environment_event(
            workflow_id,
            env_id,
            "session.unbound",
            session_name=session_name,
        )

    # -------------------------------------------------------------------------
    # Session Events (Workflow-level)
    # -------------------------------------------------------------------------

    def log_session_created(
        self, workflow_id: str, session: AgenticSessionHandle
    ) -> None:
        """Log session creation event."""
        self._append_workflow_event(
            workflow_id,
            "session.created",
            id=session.id,
            name=session.name,
            environment_id=session.environment_id,
            title=session.title,
            agent=session.agent,
            model=session.model,
        )
        # Initialize session log
        self._append_session_event(
            workflow_id,
            session.name,
            "session.created",
            id=session.id,
            environment_id=session.environment_id,
            title=session.title,
            agent=session.agent,
            model=session.model,
        )

    def log_session_status(
        self,
        workflow_id: str,
        session_name: str,
        status: AgenticSessionStatus | str,
    ) -> None:
        """Log session status change event."""
        status_str = status.value if isinstance(status, AgenticSessionStatus) else status
        self._append_workflow_event(
            workflow_id,
            "session.status",
            name=session_name,
            status=status_str,
        )
        self._append_session_event(
            workflow_id,
            session_name,
            "session.status",
            status=status_str,
        )

    def log_session_deleted(self, workflow_id: str, session_name: str) -> None:
        """Log session deletion event."""
        self._append_workflow_event(
            workflow_id,
            "session.deleted",
            name=session_name,
        )
        self._append_session_event(
            workflow_id,
            session_name,
            "session.deleted",
        )

    # -------------------------------------------------------------------------
    # Session Events (Session-level)
    # -------------------------------------------------------------------------

    def log_message_sent(
        self,
        workflow_id: str,
        session_name: str,
        content: str,
        wait: bool = False,
    ) -> None:
        """Log user message sent event."""
        # Truncate content for preview
        preview = content[:100] + "..." if len(content) > 100 else content
        self._append_session_event(
            workflow_id,
            session_name,
            "message.sent",
            role="user",
            preview=preview,
            wait=wait,
        )

    def log_message_started(self, workflow_id: str, session_name: str) -> None:
        """Log assistant started responding event."""
        self._append_session_event(
            workflow_id,
            session_name,
            "message.started",
        )

    def log_message_chunk(
        self, workflow_id: str, session_name: str, content: str
    ) -> None:
        """Log message chunk received event."""
        # Only log a preview to avoid huge logs
        preview = content[:200] if len(content) > 200 else content
        self._append_session_event(
            workflow_id,
            session_name,
            "message.chunk",
            content=preview,
        )

    def log_message_complete(
        self,
        workflow_id: str,
        session_name: str,
        tokens: int | None = None,
    ) -> None:
        """Log message complete event."""
        data = {}
        if tokens is not None:
            data["tokens"] = tokens
        self._append_session_event(
            workflow_id,
            session_name,
            "message.complete",
            **data,
        )

    def log_tool_call(
        self,
        workflow_id: str,
        session_name: str,
        tool: str,
        args: dict | None = None,
    ) -> None:
        """Log tool call event."""
        self._append_session_event(
            workflow_id,
            session_name,
            "tool.call",
            tool=tool,
            args=args or {},
        )

    def log_tool_result(
        self,
        workflow_id: str,
        session_name: str,
        tool: str,
        success: bool = True,
    ) -> None:
        """Log tool result event."""
        self._append_session_event(
            workflow_id,
            session_name,
            "tool.result",
            tool=tool,
            success=success,
        )


# =============================================================================
# Event Log Reader
# =============================================================================


class EventLogReader:
    """Reader for JSONL event logs.

    Provides methods to read and reconstruct state from event logs.
    """

    def __init__(self, state_dir: Path | str | None = None) -> None:
        """Initialize the event log reader.

        Args:
            state_dir: Directory for state files (defaults to XDG state dir)
        """
        if state_dir is None:
            xdg_state = os.environ.get(
                "XDG_STATE_HOME", str(Path.home() / ".local" / "state")
            )
            self.state_dir = Path(xdg_state) / "doeff-agentic"
        else:
            self.state_dir = Path(state_dir)

    def _workflow_dir(self, workflow_id: str) -> Path:
        """Get workflow directory path."""
        return self.state_dir / "workflows" / workflow_id

    def _read_jsonl(self, path: Path) -> list[EventLogEntry]:
        """Read all entries from a JSONL file."""
        if not path.exists():
            return []
        entries = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(EventLogEntry.from_json(line))
                    except json.JSONDecodeError:
                        continue
        return entries

    def read_workflow_events(self, workflow_id: str) -> list[EventLogEntry]:
        """Read all events from workflow log."""
        path = self._workflow_dir(workflow_id) / "workflow.jsonl"
        return self._read_jsonl(path)

    def read_session_events(
        self, workflow_id: str, session_name: str
    ) -> list[EventLogEntry]:
        """Read all events from session log."""
        path = self._workflow_dir(workflow_id) / "sessions" / f"{session_name}.jsonl"
        return self._read_jsonl(path)

    def read_environment_events(
        self, workflow_id: str, env_id: str
    ) -> list[EventLogEntry]:
        """Read all events from environment log."""
        path = self._workflow_dir(workflow_id) / "environments" / f"{env_id}.jsonl"
        return self._read_jsonl(path)

    def list_workflows(self) -> list[str]:
        """List all workflow IDs."""
        workflows_dir = self.state_dir / "workflows"
        if not workflows_dir.exists():
            return []
        return [d.name for d in workflows_dir.iterdir() if d.is_dir()]

    def list_sessions(self, workflow_id: str) -> list[str]:
        """List all session names in a workflow."""
        sessions_dir = self._workflow_dir(workflow_id) / "sessions"
        if not sessions_dir.exists():
            return []
        return [
            f.stem for f in sessions_dir.glob("*.jsonl") if f.is_file()
        ]

    def list_environments(self, workflow_id: str) -> list[str]:
        """List all environment IDs in a workflow."""
        envs_dir = self._workflow_dir(workflow_id) / "environments"
        if not envs_dir.exists():
            return []
        return [
            f.stem for f in envs_dir.glob("*.jsonl") if f.is_file()
        ]

    # -------------------------------------------------------------------------
    # State Reconstruction
    # -------------------------------------------------------------------------

    def reconstruct_workflow_state(
        self, workflow_id: str
    ) -> AgenticWorkflowHandle | None:
        """Reconstruct workflow state from event log.

        Returns None if workflow doesn't exist.
        """
        events = self.read_workflow_events(workflow_id)
        if not events:
            return None

        # Find creation event
        name: str | None = None
        metadata: dict | None = None
        status = AgenticWorkflowStatus.PENDING
        created_at: datetime | None = None

        for entry in events:
            if entry.event_type == "workflow.created":
                name = entry.data.get("name")
                metadata = entry.data.get("metadata")
                created_at = datetime.fromisoformat(entry.ts)
            elif entry.event_type == "workflow.status":
                status = AgenticWorkflowStatus(entry.data["status"])

        if created_at is None:
            return None

        return AgenticWorkflowHandle(
            id=workflow_id,
            name=name,
            status=status,
            created_at=created_at,
            metadata=metadata,
        )

    def reconstruct_session_state(
        self, workflow_id: str, session_name: str
    ) -> AgenticSessionHandle | None:
        """Reconstruct session state from event logs.

        Returns None if session doesn't exist.
        """
        # Get session info from workflow events
        workflow_events = self.read_workflow_events(workflow_id)

        session_id: str | None = None
        environment_id: str | None = None
        title: str | None = None
        agent: str | None = None
        model: str | None = None
        status = AgenticSessionStatus.PENDING
        created_at: datetime | None = None

        for entry in workflow_events:
            if entry.event_type == "session.created":
                if entry.data.get("name") == session_name:
                    session_id = entry.data.get("id")
                    environment_id = entry.data.get("environment_id")
                    title = entry.data.get("title")
                    agent = entry.data.get("agent")
                    model = entry.data.get("model")
                    created_at = datetime.fromisoformat(entry.ts)
            elif entry.event_type == "session.status":
                if entry.data.get("name") == session_name:
                    status = AgenticSessionStatus(entry.data["status"])

        if session_id is None or environment_id is None or created_at is None:
            return None

        return AgenticSessionHandle(
            id=session_id,
            name=session_name,
            workflow_id=workflow_id,
            environment_id=environment_id,
            status=status,
            created_at=created_at,
            title=title,
            agent=agent,
            model=model,
        )

    def reconstruct_environment_state(
        self, workflow_id: str, env_id: str
    ) -> AgenticEnvironmentHandle | None:
        """Reconstruct environment state from event logs.

        Returns None if environment doesn't exist.
        """
        events = self.read_environment_events(workflow_id, env_id)
        if not events:
            return None

        env_type: AgenticEnvironmentType | None = None
        name: str | None = None
        working_dir: str | None = None
        base_commit: str | None = None
        source_environment_id: str | None = None
        created_at: datetime | None = None
        deleted = False

        for entry in events:
            if entry.event_type == "environment.created":
                env_type = AgenticEnvironmentType(entry.data["env_type"])
                name = entry.data.get("name")
                working_dir = entry.data.get("working_dir")
                base_commit = entry.data.get("base_commit")
                source_environment_id = entry.data.get("source_environment_id")
                created_at = datetime.fromisoformat(entry.ts)
            elif entry.event_type == "environment.deleted":
                deleted = True

        if deleted or env_type is None or working_dir is None or created_at is None:
            return None

        return AgenticEnvironmentHandle(
            id=env_id,
            env_type=env_type,
            name=name,
            working_dir=working_dir,
            created_at=created_at,
            base_commit=base_commit,
            source_environment_id=source_environment_id,
        )

    def get_sessions_for_environment(
        self, workflow_id: str, env_id: str
    ) -> list[str]:
        """Get all session names bound to an environment."""
        events = self.read_environment_events(workflow_id, env_id)
        bound: set[str] = set()

        for entry in events:
            if entry.event_type == "session.bound":
                bound.add(entry.data["session_name"])
            elif entry.event_type == "session.unbound":
                bound.discard(entry.data["session_name"])

        return list(bound)


# =============================================================================
# Workflow Index Management
# =============================================================================


def get_default_state_dir() -> Path:
    """Get the default state directory following XDG Base Directory Specification."""
    xdg_state = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    return Path(xdg_state) / "doeff-agentic"


class WorkflowIndex:
    """Manages the workflow index for fast prefix lookup.

    The index maps workflow IDs to names and is stored as JSON for quick access.
    """

    def __init__(self, state_dir: Path | str | None = None) -> None:
        """Initialize the workflow index.

        Args:
            state_dir: Directory for state files (defaults to XDG state dir)
        """
        if state_dir is None:
            self.state_dir = get_default_state_dir()
        else:
            self.state_dir = Path(state_dir)

    def _index_path(self) -> Path:
        """Get path to the index file."""
        return self.state_dir / "index.json"

    def _load(self) -> dict[str, str]:
        """Load the workflow index."""
        path = self._index_path()
        if path.exists():
            return json.loads(path.read_text())
        return {}

    def _save(self, index: dict[str, str]) -> None:
        """Save the workflow index atomically."""
        _atomic_write(self._index_path(), json.dumps(index, indent=2))

    def add(self, workflow_id: str, name: str | None) -> None:
        """Add or update workflow in index."""
        index = self._load()
        index[workflow_id] = name or workflow_id
        self._save(index)

    def remove(self, workflow_id: str) -> None:
        """Remove workflow from index."""
        index = self._load()
        if workflow_id in index:
            del index[workflow_id]
            self._save(index)

    def resolve_prefix(self, prefix: str) -> str | None:
        """Resolve a workflow ID prefix to the full ID.

        Returns None if no match found.
        Raises ValueError if prefix is ambiguous (matches multiple workflows).
        """
        index = self._load()

        # Exact match
        if prefix in index:
            return prefix

        # Prefix match
        matches = [wf_id for wf_id in index if wf_id.startswith(prefix)]

        if len(matches) == 0:
            return None
        if len(matches) == 1:
            return matches[0]
        match_details = ", ".join(
            f"{wf_id} ({index[wf_id]})" for wf_id in matches
        )
        raise ValueError(
            f"Ambiguous prefix '{prefix}' matches multiple workflows: {match_details}"
        )

    def list_all(self) -> dict[str, str]:
        """List all workflows in index."""
        return self._load()


__all__ = [
    "EventLogEntry",
    "EventLogReader",
    "EventLogWriter",
    "WorkflowIndex",
    "get_default_state_dir",
]
