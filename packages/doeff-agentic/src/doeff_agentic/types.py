"""
Type definitions for doeff-agentic.

This module defines the core data types used throughout the package:
- WorkflowInfo: Workflow metadata
- AgentInfo: Agent state within a workflow
- WatchUpdate: Real-time workflow updates
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class WorkflowStatus(Enum):
    """Workflow execution status."""
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentStatus(Enum):
    """Agent execution status within a workflow."""
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
    """Information about an agent within a workflow.

    Attributes:
        name: Agent identifier (e.g., "review-agent")
        status: Current agent status
        session_name: Tmux session name (e.g., "doeff-a3f8b2c-review-agent")
        pane_id: Tmux pane identifier
        started_at: When the agent was started
        last_output_hash: Hash of last captured output (for change detection)
    """
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
    """Information about a workflow.

    Attributes:
        id: Short hex identifier (7 chars, e.g., "a3f8b2c")
        name: Human-readable workflow name
        status: Current workflow status
        started_at: When the workflow started
        updated_at: Last update timestamp
        current_agent: Name of the currently active agent
        agents: List of all agents in this workflow
        last_slog: Last structured log entry
        error: Error message if failed
    """
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
    """Types of watch events."""
    STATUS_CHANGE = "status_change"
    AGENT_CHANGE = "agent_change"
    SLOG = "slog"
    OUTPUT = "output"
    ERROR = "error"


@dataclass(frozen=True)
class WatchUpdate:
    """Real-time update from watching a workflow.

    Attributes:
        workflow: Current workflow state
        event: Type of event that triggered this update
        data: Event-specific data
    """
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
    """Configuration for launching an agent.

    Attributes:
        agent_type: Type of agent (claude, codex, gemini)
        prompt: Initial prompt for the agent
        profile: Agent profile to use (optional)
        resume: Whether to resume an existing session
        work_dir: Working directory for the agent
    """
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
