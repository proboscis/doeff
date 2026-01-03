"""
Effect definitions for doeff-agentic.

All effects use the `Agentic` prefix and follow the doeff effect pattern.

Effect Categories:
- Workflow: AgenticCreateWorkflow, AgenticGetWorkflow
- Environment: AgenticCreateEnvironment, AgenticGetEnvironment, AgenticDeleteEnvironment
- Session: AgenticCreateSession, AgenticForkSession, AgenticGetSession, etc.
- Message: AgenticSendMessage, AgenticGetMessages
- Event: AgenticNextEvent
- Parallel: AgenticGather, AgenticRace
- Status: AgenticGetSessionStatus, AgenticSupportsCapability
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Generator, TypeVar


from .types import AgenticEnvironmentType, AgenticSessionStatus


E = TypeVar("E", bound="AgenticEffectBase")


# =============================================================================
# Effect Base
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class AgenticEffectBase:
    """Base class for agentic effects.

    All agentic effects inherit from this class and are compatible with
    doeff's CESK interpreter through the Effect protocol.
    """

    created_at: Any = field(default=None, compare=False)  # EffectCreationContext | None

    def with_created_at(self: E, created_at: Any) -> E:  # EffectCreationContext | None
        """Return a copy with updated creation context."""
        if created_at is self.created_at:
            return self
        return replace(self, created_at=created_at)

    def intercept(self: E, transform: Callable[[Any], Any]) -> E:
        """Return a copy where any nested programs are intercepted.

        Agentic effects don't contain nested programs, so this returns self unchanged.
        Required for CESK interpreter compatibility.
        """
        return self

    def to_generator(self) -> Generator[Any, Any, Any]:
        """An Effect is a single-step program that yields itself."""
        result = yield self
        return result


# =============================================================================
# Workflow Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class AgenticCreateWorkflow(AgenticEffectBase):
    """Create a new workflow instance.

    Yields: AgenticWorkflowHandle

    Note: Usually called implicitly by the handler on first effect.
    Explicit creation allows setting workflow name/metadata upfront.
    """

    name: str | None = None  # Human-readable name
    metadata: dict[str, Any] | None = None  # Custom metadata


@dataclass(frozen=True, kw_only=True)
class AgenticGetWorkflow(AgenticEffectBase):
    """Get current workflow handle.

    Yields: AgenticWorkflowHandle
    """

    pass


# =============================================================================
# Environment Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class AgenticCreateEnvironment(AgenticEffectBase):
    """Create a new environment for agent sessions.

    Yields: AgenticEnvironmentHandle
    Raises:
        AgenticError: If worktree creation fails
        AgenticEnvironmentNotFoundError: If source_environment_id invalid (for inherited)
    """

    env_type: AgenticEnvironmentType  # Required: worktree, inherited, copy, shared
    name: str | None = None  # Human-readable name (auto-generated if None)
    base_commit: str | None = None  # For worktree/copy types (default: HEAD)
    source_environment_id: str | None = None  # For inherited/copy types
    working_dir: str | None = None  # Override working directory (for shared type)


@dataclass(frozen=True, kw_only=True)
class AgenticGetEnvironment(AgenticEffectBase):
    """Get an existing environment by ID.

    Yields: AgenticEnvironmentHandle
    Raises: AgenticEnvironmentNotFoundError
    """

    environment_id: str


@dataclass(frozen=True, kw_only=True)
class AgenticDeleteEnvironment(AgenticEffectBase):
    """Delete an environment and clean up resources.

    Yields: bool (True if deleted)
    Raises: AgenticEnvironmentInUseError (if force=False and sessions exist)
    """

    environment_id: str
    force: bool = False  # If True, delete even if sessions reference it


# =============================================================================
# Session Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class AgenticCreateSession(AgenticEffectBase):
    """Create a new agent session in an environment.

    Yields: AgenticSessionHandle
    Raises:
        AgenticDuplicateNameError: If name already exists in workflow
        AgenticEnvironmentNotFoundError: If environment_id invalid
    """

    name: str  # Required: unique identifier within workflow
    environment_id: str | None = None  # None = create implicit shared environment
    title: str | None = None  # Display title (defaults to name)
    agent: str | None = None  # Agent type (e.g., "code-review")
    model: str | None = None  # Model override


@dataclass(frozen=True, kw_only=True)
class AgenticForkSession(AgenticEffectBase):
    """Fork an existing session at a specific message.

    Yields: AgenticSessionHandle
    Raises:
        AgenticUnsupportedOperationError: On tmux handler
        AgenticSessionNotFoundError: If session_id invalid
        AgenticDuplicateNameError: If name already exists
    """

    session_id: str
    name: str  # Required: new session name
    message_id: str | None = None  # Fork point (None = latest)


@dataclass(frozen=True, kw_only=True)
class AgenticGetSession(AgenticEffectBase):
    """Get an existing session by ID or name.

    Yields: AgenticSessionHandle
    Raises: AgenticSessionNotFoundError

    Note: Exactly one of session_id or name must be provided.
    """

    session_id: str | None = None  # Global unique ID
    name: str | None = None  # Workflow-local name


@dataclass(frozen=True, kw_only=True)
class AgenticAbortSession(AgenticEffectBase):
    """Abort a running session.

    Yields: None
    Raises: AgenticSessionNotFoundError
    """

    session_id: str


@dataclass(frozen=True, kw_only=True)
class AgenticDeleteSession(AgenticEffectBase):
    """Delete a session and all its data.

    Yields: bool (True if deleted)
    Raises: AgenticSessionNotFoundError
    """

    session_id: str


# =============================================================================
# Message Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class AgenticSendMessage(AgenticEffectBase):
    """Send a message to a session.

    Yields: AgenticMessageHandle
    Raises:
        AgenticSessionNotFoundError: If session_id invalid
        AgenticSessionNotRunningError: If session not in running/blocked state

    Behavior of `wait` parameter:
        - wait=False: Returns immediately after message is sent
        - wait=True: Blocks until assistant response is complete
          (i.e., until message.complete event or session becomes blocked/done)
    """

    session_id: str
    content: str
    wait: bool = False
    agent: str | None = None  # Override agent for this message
    model: str | None = None  # Override model for this message


@dataclass(frozen=True, kw_only=True)
class AgenticGetMessages(AgenticEffectBase):
    """Get messages from a session.

    Yields: list[AgenticMessage]
    Raises: AgenticSessionNotFoundError
    """

    session_id: str
    limit: int | None = None  # None = all messages


# =============================================================================
# Event Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class AgenticNextEvent(AgenticEffectBase):
    """Wait for next event from session.

    Handler manages SSE connection internally:
    - Connection created lazily on first call per session
    - Connection reused for subsequent calls to same session
    - Connection closed when AgenticEndOfEvents returned
    - Automatic reconnection on transient failures (up to 3 retries)

    Yields: AgenticEvent | AgenticEndOfEvents
    Raises:
        AgenticSessionNotFoundError: If session_id invalid
        AgenticTimeoutError: If timeout exceeded

    Event types:
    - message.started: Assistant started generating response
    - message.chunk: Partial content received
    - message.complete: Full response received
    - tool.call: Tool invocation started
    - tool.result: Tool returned result
    - session.blocked: Session waiting for user input
    - session.error: Session encountered error
    - session.done: Session completed
    """

    session_id: str
    timeout: float | None = None  # Seconds, None = no timeout


# =============================================================================
# Parallel Execution Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class AgenticGather(AgenticEffectBase):
    """Wait for multiple sessions to complete.

    Yields: dict[str, AgenticSessionHandle]  # name -> final handle
    Raises: AgenticTimeoutError

    Completes when all specified sessions reach a terminal status
    (DONE, ERROR, or ABORTED).
    """

    session_names: tuple[str, ...]  # Session names to wait for
    timeout: float | None = None  # Total timeout for all


@dataclass(frozen=True, kw_only=True)
class AgenticRace(AgenticEffectBase):
    """Wait for first session to complete.

    Yields: tuple[str, AgenticSessionHandle]  # (name, handle) of first to complete
    Raises: AgenticTimeoutError
    """

    session_names: tuple[str, ...]
    timeout: float | None = None


# =============================================================================
# Status & Capability Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class AgenticGetSessionStatus(AgenticEffectBase):
    """Get current session status.

    Yields: AgenticSessionStatus
    Raises: AgenticSessionNotFoundError
    """

    session_id: str


@dataclass(frozen=True, kw_only=True)
class AgenticSupportsCapability(AgenticEffectBase):
    """Check if current handler supports a capability.

    Yields: bool

    Capabilities:
    - "fork": Session forking (OpenCode only)
    - "events": SSE event streaming (OpenCode only)
    - "worktree": Git worktree environments (requires git)
    - "container": Container isolation (future)
    """

    capability: str


# =============================================================================
# Legacy Effects (for backward compatibility)
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class RunAgentEffect(AgenticEffectBase):
    """Effect to run an agent to completion (deprecated).

    Use AgenticCreateSession + AgenticSendMessage(wait=True) instead.
    """

    from .types import AgentConfig

    config: AgentConfig
    session_name: str | None = None
    poll_interval: float = 1.0
    ready_timeout: float = 30.0


@dataclass(frozen=True, kw_only=True)
class SendMessageEffect(AgenticEffectBase):
    """Effect to send a message (deprecated).

    Use AgenticSendMessage instead.
    """

    session_name: str
    message: str
    enter: bool = True


@dataclass(frozen=True, kw_only=True)
class WaitForStatusEffect(AgenticEffectBase):
    """Effect to wait for status (deprecated).

    Use AgenticNextEvent loop instead.
    """

    from .types import AgentStatus

    session_name: str
    target_status: AgentStatus | tuple[AgentStatus, ...]
    timeout: float = 300.0
    poll_interval: float = 1.0


@dataclass(frozen=True, kw_only=True)
class CaptureOutputEffect(AgenticEffectBase):
    """Effect to capture output (deprecated).

    Use AgenticGetMessages instead.
    """

    session_name: str
    lines: int = 100


@dataclass(frozen=True, kw_only=True)
class WaitForUserInputEffect(AgenticEffectBase):
    """Effect to wait for user input (deprecated)."""

    session_name: str
    prompt: str
    timeout: float | None = None


@dataclass(frozen=True, kw_only=True)
class StopAgentEffect(AgenticEffectBase):
    """Effect to stop agent (deprecated).

    Use AgenticAbortSession instead.
    """

    session_name: str


# =============================================================================
# Legacy Effect Constructors (for backward compatibility)
# =============================================================================


def RunAgent(  # noqa: N802
    config: Any,
    *,
    session_name: str | None = None,
    poll_interval: float = 1.0,
    ready_timeout: float = 30.0,
) -> RunAgentEffect:
    """Run an agent to completion (deprecated)."""
    return RunAgentEffect(
        config=config,
        session_name=session_name,
        poll_interval=poll_interval,
        ready_timeout=ready_timeout,
    )


def SendMessage(  # noqa: N802
    session_name: str,
    message: str,
    *,
    enter: bool = True,
) -> SendMessageEffect:
    """Send a message (deprecated)."""
    return SendMessageEffect(
        session_name=session_name,
        message=message,
        enter=enter,
    )


def WaitForStatus(  # noqa: N802
    session_name: str,
    target_status: Any,
    *,
    timeout: float = 300.0,
    poll_interval: float = 1.0,
) -> WaitForStatusEffect:
    """Wait for status (deprecated)."""
    return WaitForStatusEffect(
        session_name=session_name,
        target_status=target_status,
        timeout=timeout,
        poll_interval=poll_interval,
    )


def CaptureOutput(  # noqa: N802
    session_name: str,
    *,
    lines: int = 100,
) -> CaptureOutputEffect:
    """Capture output (deprecated)."""
    return CaptureOutputEffect(
        session_name=session_name,
        lines=lines,
    )


def WaitForUserInput(  # noqa: N802
    session_name: str,
    prompt: str,
    *,
    timeout: float | None = None,
) -> WaitForUserInputEffect:
    """Wait for user input (deprecated)."""
    return WaitForUserInputEffect(
        session_name=session_name,
        prompt=prompt,
        timeout=timeout,
    )


def StopAgent(session_name: str) -> StopAgentEffect:  # noqa: N802
    """Stop agent (deprecated)."""
    return StopAgentEffect(session_name=session_name)


# =============================================================================
# Re-export exceptions from exceptions module
# =============================================================================

from .exceptions import (
    AgenticError,
    AgenticSessionNotFoundError,
    AgenticEnvironmentNotFoundError,
    AgenticWorkflowNotFoundError,
    AgenticSessionNotRunningError,
    AgenticEnvironmentInUseError,
    AgenticUnsupportedOperationError,
    AgenticServerError,
    AgenticTimeoutError,
    AgenticDuplicateNameError,
    AgenticAmbiguousPrefixError,
)

# Legacy error aliases
WorkflowNotFoundError = AgenticWorkflowNotFoundError
AgentNotRunningError = AgenticSessionNotRunningError
UserInputTimeoutError = AgenticTimeoutError
AmbiguousPrefixError = AgenticAmbiguousPrefixError


__all__ = [
    # Effect base
    "AgenticEffectBase",
    # Workflow effects
    "AgenticCreateWorkflow",
    "AgenticGetWorkflow",
    # Environment effects
    "AgenticCreateEnvironment",
    "AgenticGetEnvironment",
    "AgenticDeleteEnvironment",
    # Session effects
    "AgenticCreateSession",
    "AgenticForkSession",
    "AgenticGetSession",
    "AgenticAbortSession",
    "AgenticDeleteSession",
    # Message effects
    "AgenticSendMessage",
    "AgenticGetMessages",
    # Event effects
    "AgenticNextEvent",
    # Parallel effects
    "AgenticGather",
    "AgenticRace",
    # Status effects
    "AgenticGetSessionStatus",
    "AgenticSupportsCapability",
    # Legacy effects (deprecated)
    "RunAgentEffect",
    "SendMessageEffect",
    "WaitForStatusEffect",
    "CaptureOutputEffect",
    "WaitForUserInputEffect",
    "StopAgentEffect",
    # Legacy constructors (deprecated)
    "RunAgent",
    "SendMessage",
    "WaitForStatus",
    "CaptureOutput",
    "WaitForUserInput",
    "StopAgent",
    # Exceptions
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
    # Legacy error aliases
    "WorkflowNotFoundError",
    "AgentNotRunningError",
    "UserInputTimeoutError",
    "AmbiguousPrefixError",
]
