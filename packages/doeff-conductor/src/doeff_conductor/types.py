"""
Type definitions for doeff-conductor.

This module defines the core data types for conductor orchestration:
- Issue: Issue from vault with YAML frontmatter
- Workspace: Logical mutable state handle
- WorkflowHandle: Workflow instance handle
- AgentRef: Reference to a running agent
- PRHandle: Pull request handle
"""


from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
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
    STOPPED = "stopped"
    ABORTED = "aborted"

    def is_terminal(self) -> bool:
        """Check if this is a terminal status."""
        return self in (
            WorkflowStatus.DONE,
            WorkflowStatus.ERROR,
            WorkflowStatus.STOPPED,
            WorkflowStatus.ABORTED,
        )


class MergeStrategy(Enum):
    """Strategy for merging branches."""

    MERGE = "merge"  # Standard merge commit
    REBASE = "rebase"  # Rebase onto target
    SQUASH = "squash"  # Squash all commits


class MergeStatus(Enum):
    """Structured outcome for workspace reconciliation."""

    MERGED = "merged"
    CONFLICT = "conflict"


# =============================================================================
# Gate Types
# =============================================================================


@dataclass(frozen=True)
class ExecResult:
    """Structured result from a deterministic gate command."""

    exit_code: int
    log_path: str
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        """Return True when the command completed successfully."""
        return self.exit_code == 0 and not self.timed_out


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
# Workspace Types
# =============================================================================


@dataclass(frozen=True)
class Workspace:
    """Logical unit of mutable state for a workflow.

    For the git medium family, the portable identity is ``(repo, ref)``.
    Site-local materialization paths are handler-private and intentionally
    absent from this value.
    """

    id: str  # Unique workspace ID
    repo: str  # Repository name resolved by the handler environment
    ref: str  # Portable git ref for this workspace
    base_ref: str  # Ref this workspace was created from
    issue_id: str | None = None  # Associated issue ID
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "repo": self.repo,
            "ref": self.ref,
            "base_ref": self.base_ref,
            "issue_id": self.issue_id,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Workspace":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            repo=data["repo"],
            ref=data["ref"],
            base_ref=data["base_ref"],
            issue_id=data.get("issue_id"),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


@dataclass(frozen=True)
class MergeConflict:
    """Details for one workspace that could not be reconciled cleanly."""

    workspace: Workspace
    files: tuple[str, ...]


@dataclass(frozen=True)
class MergeWorkspacesResult:
    """Structured result from a deterministic workspace reconciliation."""

    status: MergeStatus
    workspace: Workspace | None
    conflicts: tuple[MergeConflict, ...] = ()
    log_path: str | None = None
    message: str | None = None

    @property
    def merged(self) -> bool:
        """Return True when reconciliation produced a clean workspace."""
        return self.status is MergeStatus.MERGED


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
    workspace_id: str  # Workspace the agent runs in
    agent_type: str = "claude"  # Agent type (claude, codex, gemini)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "workflow_id": self.workflow_id,
            "workspace_id": self.workspace_id,
            "agent_type": self.agent_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentRef":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            workflow_id=data["workflow_id"],
            workspace_id=data["workspace_id"],
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
    workspaces: tuple[str, ...] = ()  # Workspace IDs
    agents: tuple[str, ...] = ()  # Agent session IDs
    pr_url: str | None = None  # Resulting PR URL
    error: str | None = None  # Error message if failed
    result_payload: Any | None = None  # Workflow return payload when JSON-serializable

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
            "workspaces": list(self.workspaces),
            "agents": list(self.agents),
            "pr_url": self.pr_url,
            "error": self.error,
            "result_payload": _jsonable(self.result_payload),
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
            workspaces=tuple(data.get("workspaces", [])),
            agents=tuple(data.get("agents", [])),
            pr_url=data.get("pr_url"),
            error=data.get("error"),
            result_payload=data.get("result_payload"),
        )


__all__ = [  # noqa: RUF022
    # Agent types
    "AgentRef",
    "ExecResult",
    # Issue types
    "Issue",
    # Enums
    "IssueStatus",
    "MergeConflict",
    "MergeStrategy",
    "MergeStatus",
    "MergeWorkspacesResult",
    # Git types
    "PRHandle",
    "Workspace",
    # Workflow types
    "WorkflowHandle",
    "WorkflowStatus",
]


def _jsonable(value: Any) -> Any:  # noqa: PLR0911
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, type):
        return value.__name__
    if hasattr(value, "to_dict"):
        to_dict = value.to_dict
        if callable(to_dict):
            return _jsonable(to_dict())
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)
