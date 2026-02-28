"""
Type definitions for doeff-conductor.

This module defines the core data types for conductor orchestration:
- Issue: Issue from vault with YAML frontmatter
- WorktreeEnv: Git worktree environment handle
- WorkflowHandle: Workflow instance handle
- AgentRef: Reference to a running agent
- PRHandle: Pull request handle
"""


from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

# =============================================================================
# Enums
# =============================================================================


class IssueStatus(Enum):
    """Status of an issue in the vault."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class WorkflowStatus(Enum):
    """Status of a conductor workflow."""

    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    DONE = "done"
    ERROR = "error"
    ABORTED = "aborted"

    def is_terminal(self) -> bool:
        """Check if this is a terminal status."""
        return self in (
            WorkflowStatus.DONE,
            WorkflowStatus.ERROR,
            WorkflowStatus.ABORTED,
        )


class MergeStrategy(Enum):
    """Strategy for merging branches."""

    MERGE = "merge"  # Standard merge commit
    REBASE = "rebase"  # Rebase onto target
    SQUASH = "squash"  # Squash all commits


# =============================================================================
# Issue Types
# =============================================================================


@dataclass
class Issue:
    """Issue from the vault with YAML frontmatter.

    Issues are markdown files with YAML frontmatter containing metadata.
    The body is the issue description/instructions.

    Example file:
        ---
        id: ISSUE-001
        title: Add login feature
        status: open
        labels: [feature, auth]
        created: 2025-01-10
        ---

        ## Description
        Implement user login with OAuth2...
    """

    id: str
    title: str
    body: str
    status: IssueStatus = IssueStatus.OPEN
    labels: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime | None = None
    resolved_at: datetime | None = None
    pr_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "status": self.status.value,
            "labels": list(self.labels),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "pr_url": self.pr_url,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Issue":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            title=data["title"],
            body=data["body"],
            status=IssueStatus(data["status"]),
            labels=tuple(data.get("labels", [])),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=(
                datetime.fromisoformat(data["updated_at"])
                if data.get("updated_at")
                else None
            ),
            resolved_at=(
                datetime.fromisoformat(data["resolved_at"])
                if data.get("resolved_at")
                else None
            ),
            pr_url=data.get("pr_url"),
            metadata=data.get("metadata", {}),
        )


# =============================================================================
# Environment Types
# =============================================================================


@dataclass
class WorktreeEnv:
    """Handle to a git worktree environment.

    Represents an isolated working directory created from a git worktree.
    Each agent can work in its own worktree without conflicts.
    """

    id: str  # Unique environment ID
    path: Path  # Absolute path to worktree directory
    branch: str  # Branch name for this worktree
    base_commit: str  # Commit SHA the worktree was created from
    issue_id: str | None = None  # Associated issue ID
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "path": str(self.path),
            "branch": self.branch,
            "base_commit": self.base_commit,
            "issue_id": self.issue_id,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorktreeEnv":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            path=Path(data["path"]),
            branch=data["branch"],
            base_commit=data["base_commit"],
            issue_id=data.get("issue_id"),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


# =============================================================================
# Agent Types
# =============================================================================


@dataclass
class AgentRef:
    """Reference to a running agent session.

    Used to interact with agents after spawning (send messages, wait for status, etc).
    """

    id: str  # Agent session ID
    name: str  # Human-readable name
    workflow_id: str  # Parent workflow ID
    env_id: str  # Environment the agent runs in
    agent_type: str = "claude"  # Agent type (claude, codex, gemini)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "workflow_id": self.workflow_id,
            "env_id": self.env_id,
            "agent_type": self.agent_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentRef":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            workflow_id=data["workflow_id"],
            env_id=data["env_id"],
            agent_type=data.get("agent_type", "claude"),
        )


# =============================================================================
# Git Types
# =============================================================================


@dataclass
class PRHandle:
    """Handle to a pull request."""

    url: str  # Full PR URL
    number: int  # PR number
    title: str  # PR title
    branch: str  # Source branch
    target: str  # Target branch (e.g., main)
    status: str = "open"  # open, merged, closed
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "url": self.url,
            "number": self.number,
            "title": self.title,
            "branch": self.branch,
            "target": self.target,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PRHandle":
        """Create from dictionary."""
        return cls(
            url=data["url"],
            number=data["number"],
            title=data["title"],
            branch=data["branch"],
            target=data["target"],
            status=data.get("status", "open"),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


# =============================================================================
# Workflow Types
# =============================================================================


@dataclass
class WorkflowHandle:
    """Handle to a conductor workflow instance."""

    id: str  # Workflow ID (7-char hex)
    name: str  # Human-readable name
    status: WorkflowStatus
    template: str | None = None  # Template name if using template
    issue_id: str | None = None  # Associated issue ID
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    environments: tuple[str, ...] = ()  # Environment IDs
    agents: tuple[str, ...] = ()  # Agent session IDs
    pr_url: str | None = None  # Resulting PR URL
    error: str | None = None  # Error message if failed

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "template": self.template,
            "issue_id": self.issue_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "environments": list(self.environments),
            "agents": list(self.agents),
            "pr_url": self.pr_url,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowHandle":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            status=WorkflowStatus(data["status"]),
            template=data.get("template"),
            issue_id=data.get("issue_id"),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            environments=tuple(data.get("environments", [])),
            agents=tuple(data.get("agents", [])),
            pr_url=data.get("pr_url"),
            error=data.get("error"),
        )


__all__ = [
    # Agent types
    "AgentRef",
    # Issue types
    "Issue",
    # Enums
    "IssueStatus",
    "MergeStrategy",
    # Git types
    "PRHandle",
    # Workflow types
    "WorkflowHandle",
    "WorkflowStatus",
    # Environment types
    "WorktreeEnv",
]
