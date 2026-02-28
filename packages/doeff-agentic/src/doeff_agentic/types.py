"""
Type definitions for doeff-agentic.

This module defines the core data types used throughout the package:
- AgenticWorkflowHandle: Workflow metadata
- AgenticSessionHandle: Session within a workflow
- AgenticEnvironmentHandle: Environment context
- AgenticMessage: Message content
- AgenticEvent: Event from session stream
"""


from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# =============================================================================
# Enums
# =============================================================================


class AgenticEnvironmentType(Enum):
    """Type of environment for agent sessions."""

    WORKTREE = "worktree"  # Fresh git worktree at specific commit
    INHERITED = "inherited"  # Reuses working directory from another environment
    COPY = "copy"  # Copy of directory at point in time
    SHARED = "shared"  # Multiple agents same directory


class AgenticSessionStatus(Enum):
    """Session status enum.

    State transitions:
        PENDING -> BOOTING -> RUNNING <-> BLOCKED -> DONE
                                     \\-> ERROR
                                     \\-> ABORTED (via abort)
    """

    PENDING = "pending"  # Created but not started
    BOOTING = "booting"  # Starting up
    RUNNING = "running"  # Actively processing
    BLOCKED = "blocked"  # Waiting for user input
    DONE = "done"  # Completed successfully
    ERROR = "error"  # Failed with error
    ABORTED = "aborted"  # Manually aborted

    def is_terminal(self) -> bool:
        """Check if this is a terminal status."""
        return self in (
            AgenticSessionStatus.DONE,
            AgenticSessionStatus.ERROR,
            AgenticSessionStatus.ABORTED,
        )


class AgenticWorkflowStatus(Enum):
    """Workflow status enum."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    ABORTED = "aborted"

    def is_terminal(self) -> bool:
        """Check if this is a terminal status."""
        return self in (
            AgenticWorkflowStatus.DONE,
            AgenticWorkflowStatus.ERROR,
            AgenticWorkflowStatus.ABORTED,
        )


# =============================================================================
# Handles
# =============================================================================


@dataclass
class AgenticWorkflowHandle:
    """Handle to a workflow instance."""

    id: str  # 7-char hex ID
    name: str | None
    status: AgenticWorkflowStatus
    created_at: datetime
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgenticWorkflowHandle":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data.get("name"),
            status=AgenticWorkflowStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            metadata=data.get("metadata"),
        )


@dataclass
class AgenticEnvironmentHandle:
    """Handle to an environment."""

    id: str
    env_type: AgenticEnvironmentType
    name: str | None
    working_dir: str
    created_at: datetime
    base_commit: str | None = None
    source_environment_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "env_type": self.env_type.value,
            "name": self.name,
            "working_dir": self.working_dir,
            "created_at": self.created_at.isoformat(),
            "base_commit": self.base_commit,
            "source_environment_id": self.source_environment_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgenticEnvironmentHandle":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            env_type=AgenticEnvironmentType(data["env_type"]),
            name=data.get("name"),
            working_dir=data["working_dir"],
            created_at=datetime.fromisoformat(data["created_at"]),
            base_commit=data.get("base_commit"),
            source_environment_id=data.get("source_environment_id"),
        )


@dataclass
class AgenticSessionHandle:
    """Handle to an agent session."""

    id: str  # Global unique ID (from OpenCode)
    name: str  # Workflow-local identifier (user-provided)
    workflow_id: str
    environment_id: str
    status: AgenticSessionStatus
    created_at: datetime
    title: str | None = None
    agent: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "workflow_id": self.workflow_id,
            "environment_id": self.environment_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "title": self.title,
            "agent": self.agent,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgenticSessionHandle":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            workflow_id=data["workflow_id"],
            environment_id=data["environment_id"],
            status=AgenticSessionStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            title=data.get("title"),
            agent=data.get("agent"),
            model=data.get("model"),
        )


@dataclass
class AgenticMessageHandle:
    """Handle to a message."""

    id: str
    session_id: str
    role: str  # "user" | "assistant"
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgenticMessageHandle":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            session_id=data["session_id"],
            role=data["role"],
            created_at=datetime.fromisoformat(data["created_at"]),
        )


@dataclass
class AgenticMessage:
    """Full message content."""

    id: str
    session_id: str
    role: str
    content: str
    created_at: datetime
    parts: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "parts": self.parts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgenticMessage":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            session_id=data["session_id"],
            role=data["role"],
            content=data["content"],
            created_at=datetime.fromisoformat(data["created_at"]),
            parts=data.get("parts"),
        )


# =============================================================================
# Events
# =============================================================================


@dataclass
class AgenticEvent:
    """Event from session stream."""

    event_type: str  # e.g., "message.chunk", "session.done"
    session_id: str
    data: dict[str, Any]
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "event_type": self.event_type,
            "session_id": self.session_id,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgenticEvent":
        """Create from dictionary."""
        return cls(
            event_type=data["event_type"],
            session_id=data["session_id"],
            data=data["data"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )


@dataclass(frozen=True)
class AgenticEndOfEvents:
    """Sentinel indicating end of event stream.

    Returned when:
    - Session reaches terminal state (done, error, aborted)
    - SSE connection is closed by server
    """

    reason: str  # "session_done" | "session_error" | "connection_closed"
    final_status: AgenticSessionStatus | None = None


# =============================================================================
# Legacy types (for backward compatibility during migration)
# =============================================================================


class WorkflowStatus(Enum):
    """Workflow execution status (deprecated - use AgenticWorkflowStatus)."""

    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class AgentStatus(Enum):
    """Agent execution status (deprecated - use AgenticSessionStatus)."""

    PENDING = "pending"
    BOOTING = "booting"
    RUNNING = "running"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    EXITED = "exited"
    STOPPED = "stopped"


@dataclass(frozen=True)
class AgentInfo:
    """Information about an agent (deprecated - use AgenticSessionHandle)."""

    name: str
    status: AgentStatus
    session_name: str
    pane_id: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_output_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "status": self.status.value,
            "session_name": self.session_name,
            "pane_id": self.pane_id,
            "started_at": self.started_at.isoformat(),
            "last_output_hash": self.last_output_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentInfo":
        """Create from dictionary."""
        return cls(
            name=data["name"],
            status=AgentStatus(data["status"]),
            session_name=data["session_name"],
            pane_id=data.get("pane_id"),
            started_at=datetime.fromisoformat(data["started_at"]),
            last_output_hash=data.get("last_output_hash"),
        )


@dataclass(frozen=True)
class WorkflowInfo:
    """Information about a workflow (deprecated - use AgenticWorkflowHandle)."""

    id: str
    name: str
    status: WorkflowStatus
    started_at: datetime
    updated_at: datetime
    current_agent: str | None = None
    agents: tuple[AgentInfo, ...] = ()
    last_slog: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "current_agent": self.current_agent,
            "agents": [a.to_dict() for a in self.agents],
            "last_slog": self.last_slog,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowInfo":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            status=WorkflowStatus(data["status"]),
            started_at=datetime.fromisoformat(data["started_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            current_agent=data.get("current_agent"),
            agents=tuple(AgentInfo.from_dict(a) for a in data.get("agents", [])),
            last_slog=data.get("last_slog"),
            error=data.get("error"),
        )


class WatchEventType(Enum):
    """Types of watch events (deprecated)."""

    STATUS_CHANGE = "status_change"
    AGENT_CHANGE = "agent_change"
    SLOG = "slog"
    OUTPUT = "output"
    ERROR = "error"


@dataclass(frozen=True)
class WatchUpdate:
    """Real-time update from watching a workflow (deprecated)."""

    workflow: WorkflowInfo
    event: WatchEventType
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "workflow": self.workflow.to_dict(),
            "event": self.event.value,
            "data": self.data,
        }


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for launching an agent (deprecated)."""

    agent_type: str
    prompt: str
    profile: str | None = None
    resume: bool = False
    work_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "agent_type": self.agent_type,
            "prompt": self.prompt,
            "profile": self.profile,
            "resume": self.resume,
            "work_dir": self.work_dir,
        }


__all__ = [
    "AgentConfig",
    "AgentInfo",
    "AgentStatus",
    "AgenticEndOfEvents",
    "AgenticEnvironmentHandle",
    # New types (spec-compliant)
    "AgenticEnvironmentType",
    "AgenticEvent",
    "AgenticMessage",
    "AgenticMessageHandle",
    "AgenticSessionHandle",
    "AgenticSessionStatus",
    "AgenticWorkflowHandle",
    "AgenticWorkflowStatus",
    "WatchEventType",
    "WatchUpdate",
    "WorkflowInfo",
    # Legacy types (for backward compatibility)
    "WorkflowStatus",
]
