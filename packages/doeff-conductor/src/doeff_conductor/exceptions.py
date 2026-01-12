"""
Domain exceptions for doeff-conductor.

Provides a hierarchy of exceptions for specific error handling in workflows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ConductorError(Exception):
    """Base exception for all conductor errors.

    All conductor-specific exceptions inherit from this class,
    allowing workflows to catch conductor errors specifically.
    """

    pass


class IssueNotFoundError(ConductorError):
    """Raised when an issue cannot be found.

    Attributes:
        issue_id: The ID of the issue that was not found.
    """

    def __init__(self, issue_id: str, message: str | None = None):
        self.issue_id = issue_id
        super().__init__(message or f"Issue not found: {issue_id}")


class IssueAlreadyExistsError(ConductorError):
    """Raised when trying to create an issue that already exists.

    Attributes:
        issue_id: The ID of the issue that already exists.
    """

    def __init__(self, issue_id: str, message: str | None = None):
        self.issue_id = issue_id
        super().__init__(message or f"Issue already exists: {issue_id}")


@dataclass
class GitCommandError(ConductorError):
    """Raised when a git command fails.

    Provides detailed context about the failed command for debugging.

    Attributes:
        command: The command that was executed (list of args).
        returncode: The exit code from the command.
        stdout: Standard output from the command.
        stderr: Standard error from the command.
        cwd: Working directory where command was run.
    """

    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    cwd: str | None = None

    def __post_init__(self):
        # Build descriptive message
        cmd_str = " ".join(self.command)
        parts = [f"Git command failed: {cmd_str}"]
        parts.append(f"Exit code: {self.returncode}")
        if self.cwd:
            parts.append(f"Working directory: {self.cwd}")
        if self.stderr:
            parts.append(f"stderr: {self.stderr}")
        if self.stdout:
            parts.append(f"stdout: {self.stdout}")
        super().__init__("\n".join(parts))

    @classmethod
    def from_subprocess_error(
        cls,
        error: "subprocess.CalledProcessError",
        cwd: str | None = None,
    ) -> "GitCommandError":
        """Create from a subprocess.CalledProcessError.

        Args:
            error: The subprocess error.
            cwd: Working directory (optional, for context).

        Returns:
            A GitCommandError with details from the subprocess error.
        """
        import subprocess

        return cls(
            command=list(error.cmd) if isinstance(error.cmd, (list, tuple)) else [str(error.cmd)],
            returncode=error.returncode,
            stdout=error.stdout if isinstance(error.stdout, str) else (error.stdout.decode() if error.stdout else ""),
            stderr=error.stderr if isinstance(error.stderr, str) else (error.stderr.decode() if error.stderr else ""),
            cwd=cwd,
        )


class WorktreeError(ConductorError):
    """Raised when a worktree operation fails.

    Attributes:
        worktree_id: The ID of the worktree involved.
        operation: The operation that failed (create, delete, merge).
    """

    def __init__(
        self,
        worktree_id: str | None = None,
        operation: str | None = None,
        message: str | None = None,
    ):
        self.worktree_id = worktree_id
        self.operation = operation
        if message:
            super().__init__(message)
        else:
            parts = ["Worktree operation failed"]
            if operation:
                parts[0] = f"Worktree {operation} failed"
            if worktree_id:
                parts.append(f"worktree_id={worktree_id}")
            super().__init__(": ".join(parts))


class AgentError(ConductorError):
    """Raised when an agent operation fails.

    Attributes:
        agent_id: The ID of the agent involved.
        operation: The operation that failed.
    """

    def __init__(
        self,
        agent_id: str | None = None,
        operation: str | None = None,
        message: str | None = None,
    ):
        self.agent_id = agent_id
        self.operation = operation
        if message:
            super().__init__(message)
        else:
            parts = ["Agent operation failed"]
            if operation:
                parts[0] = f"Agent {operation} failed"
            if agent_id:
                parts.append(f"agent_id={agent_id}")
            super().__init__(": ".join(parts))


class AgentTimeoutError(AgentError):
    """Raised when waiting for an agent times out.

    Attributes:
        agent_id: The ID of the agent.
        timeout: The timeout value in seconds.
        last_status: The last known status of the agent.
    """

    def __init__(
        self,
        agent_id: str,
        timeout: float,
        last_status: str | None = None,
    ):
        self.timeout = timeout
        self.last_status = last_status
        message = f"Agent {agent_id} timed out after {timeout}s"
        if last_status:
            message += f" (last status: {last_status})"
        super().__init__(agent_id=agent_id, operation="wait", message=message)


class PRError(ConductorError):
    """Raised when a PR operation fails.

    Attributes:
        pr_number: The PR number involved.
        operation: The operation that failed (create, merge).
    """

    def __init__(
        self,
        pr_number: int | None = None,
        operation: str | None = None,
        message: str | None = None,
    ):
        self.pr_number = pr_number
        self.operation = operation
        if message:
            super().__init__(message)
        else:
            parts = ["PR operation failed"]
            if operation:
                parts[0] = f"PR {operation} failed"
            if pr_number:
                parts.append(f"PR #{pr_number}")
            super().__init__(": ".join(parts))


__all__ = [
    "ConductorError",
    "IssueNotFoundError",
    "IssueAlreadyExistsError",
    "GitCommandError",
    "WorktreeError",
    "AgentError",
    "AgentTimeoutError",
    "PRError",
]
