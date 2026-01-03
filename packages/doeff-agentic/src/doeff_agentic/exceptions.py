"""
Exception types for doeff-agentic.

All exceptions inherit from AgenticError base class.
"""

from __future__ import annotations


class AgenticError(Exception):
    """Base exception for all agentic errors."""

    pass


class AgenticSessionNotFoundError(AgenticError):
    """Session with given ID or name not found."""

    def __init__(self, identifier: str, by_name: bool = False) -> None:
        field = "name" if by_name else "id"
        super().__init__(f"Session with {field} '{identifier}' not found")
        self.identifier = identifier
        self.by_name = by_name


class AgenticEnvironmentNotFoundError(AgenticError):
    """Environment with given ID not found."""

    def __init__(self, environment_id: str) -> None:
        super().__init__(f"Environment with id '{environment_id}' not found")
        self.environment_id = environment_id


class AgenticWorkflowNotFoundError(AgenticError):
    """Workflow with given ID not found."""

    def __init__(self, workflow_id: str) -> None:
        super().__init__(f"Workflow with id '{workflow_id}' not found")
        self.workflow_id = workflow_id


class AgenticSessionNotRunningError(AgenticError):
    """Operation requires session to be running."""

    def __init__(self, session_id: str, current_status: str) -> None:
        super().__init__(
            f"Session '{session_id}' is not running (status: {current_status})"
        )
        self.session_id = session_id
        self.current_status = current_status


class AgenticEnvironmentInUseError(AgenticError):
    """Cannot delete environment while sessions are using it."""

    def __init__(self, environment_id: str, session_names: list[str]) -> None:
        super().__init__(
            f"Environment '{environment_id}' is in use by sessions: {session_names}"
        )
        self.environment_id = environment_id
        self.session_names = session_names


class AgenticUnsupportedOperationError(AgenticError):
    """Operation not supported by current handler (e.g., fork on tmux)."""

    def __init__(self, operation: str, handler: str, reason: str | None = None) -> None:
        msg = f"Operation '{operation}' not supported by {handler} handler"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.operation = operation
        self.handler = handler
        self.reason = reason


class AgenticServerError(AgenticError):
    """OpenCode server error or unavailable."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AgenticTimeoutError(AgenticError):
    """Operation timed out."""

    def __init__(self, operation: str, timeout: float) -> None:
        super().__init__(f"Operation '{operation}' timed out after {timeout}s")
        self.operation = operation
        self.timeout = timeout


class AgenticDuplicateNameError(AgenticError):
    """Session name already exists in workflow."""

    def __init__(self, name: str, workflow_id: str) -> None:
        super().__init__(
            f"Session name '{name}' already exists in workflow '{workflow_id}'"
        )
        self.name = name
        self.workflow_id = workflow_id


class AgenticAmbiguousPrefixError(AgenticError):
    """Workflow ID prefix matches multiple workflows."""

    def __init__(self, prefix: str, matches: list[str]) -> None:
        super().__init__(
            f"Prefix '{prefix}' matches multiple workflows: {matches}"
        )
        self.prefix = prefix
        self.matches = matches


__all__ = [
    "AgenticError",
    "AgenticSessionNotFoundError",
    "AgenticEnvironmentNotFoundError",
    "AgenticWorkflowNotFoundError",
    "AgenticSessionNotRunningError",
    "AgenticEnvironmentInUseError",
    "AgenticUnsupportedOperationError",
    "AgenticServerError",
    "AgenticTimeoutError",
    "AgenticDuplicateNameError",
    "AgenticAmbiguousPrefixError",
]
