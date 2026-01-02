"""
High-level effects for agent-based workflow orchestration.

This module provides workflow-level effects that orchestrate agent sessions
with state management and observability.

Key Effects:
- RunAgent: Launch agent, wait for completion, return result
- SendMessage: Send message to running agent session
- WaitForStatus: Wait for agent to reach specific status
- CaptureOutput: Get current agent output
- WaitForUserInput: Pause workflow for human review
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .types import AgentConfig, AgentStatus

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff.types import Effect, EffectCreationContext


# =============================================================================
# Effect Base
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class AgenticEffectBase:
    """Base class for agentic workflow effects.

    Similar to AgentEffectBase but for workflow-level orchestration.
    """

    created_at: EffectCreationContext | None = field(default=None, compare=False)

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> AgenticEffectBase:
        """Return self - agentic effects don't contain nested programs."""
        return self

    def with_created_at(
        self, created_at: EffectCreationContext | None
    ) -> AgenticEffectBase:
        """Return a copy with updated creation context."""
        new = object.__new__(self.__class__)
        for fld in self.__dataclass_fields__:
            val = created_at if fld == "created_at" else getattr(self, fld)
            object.__setattr__(new, fld, val)
        return new


# =============================================================================
# Workflow Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class RunAgentEffect(AgenticEffectBase):
    """Effect to run an agent to completion.

    This is the primary effect for agent workflows. It:
    1. Launches the agent session
    2. Monitors until terminal status
    3. Captures and returns the output

    Yields: str (agent output)
    """

    config: AgentConfig
    session_name: str | None = None
    poll_interval: float = 1.0
    ready_timeout: float = 30.0


@dataclass(frozen=True, kw_only=True)
class SendMessageEffect(AgenticEffectBase):
    """Effect to send a message to a running agent.

    Use this to provide input or guidance to an agent during execution.

    Yields: None
    """

    session_name: str
    message: str
    enter: bool = True


@dataclass(frozen=True, kw_only=True)
class WaitForStatusEffect(AgenticEffectBase):
    """Effect to wait for agent to reach a specific status.

    Useful for synchronizing with agent state.

    Yields: AgentStatus (the reached status)
    """

    session_name: str
    target_status: AgentStatus | tuple[AgentStatus, ...]
    timeout: float = 300.0
    poll_interval: float = 1.0


@dataclass(frozen=True, kw_only=True)
class CaptureOutputEffect(AgenticEffectBase):
    """Effect to capture current agent output.

    Non-blocking - returns immediately with current output.

    Yields: str (captured output)
    """

    session_name: str
    lines: int = 100


@dataclass(frozen=True, kw_only=True)
class WaitForUserInputEffect(AgenticEffectBase):
    """Effect to pause workflow for human input.

    The workflow will block until the user provides input via:
    - CLI command (doeff-agentic send)
    - TUI interface
    - Direct tmux attach

    Yields: str (user input)
    """

    session_name: str
    prompt: str
    timeout: float | None = None  # None = wait forever


@dataclass(frozen=True, kw_only=True)
class StopAgentEffect(AgenticEffectBase):
    """Effect to stop a running agent.

    Yields: None
    """

    session_name: str


# =============================================================================
# Effect Constructors (PascalCase following doeff convention)
# =============================================================================


def RunAgent(  # noqa: N802
    config: AgentConfig,
    *,
    session_name: str | None = None,
    poll_interval: float = 1.0,
    ready_timeout: float = 30.0,
) -> RunAgentEffect:
    """Run an agent to completion.

    Args:
        config: Agent configuration (type, prompt, profile, etc.)
        session_name: Optional session name (auto-generated if not provided)
        poll_interval: How often to poll for status changes
        ready_timeout: How long to wait for agent to become ready

    Returns:
        Effect that yields the agent's output
    """
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
    """Send a message to a running agent.

    Args:
        session_name: Agent session to send to
        message: Message content
        enter: Whether to press Enter after the message

    Returns:
        Effect that yields None
    """
    return SendMessageEffect(
        session_name=session_name,
        message=message,
        enter=enter,
    )


def WaitForStatus(  # noqa: N802
    session_name: str,
    target_status: AgentStatus | tuple[AgentStatus, ...],
    *,
    timeout: float = 300.0,
    poll_interval: float = 1.0,
) -> WaitForStatusEffect:
    """Wait for agent to reach a specific status.

    Args:
        session_name: Agent session to monitor
        target_status: Status(es) to wait for
        timeout: Maximum time to wait
        poll_interval: How often to check status

    Returns:
        Effect that yields the reached status
    """
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
    """Capture current agent output.

    Args:
        session_name: Agent session to capture from
        lines: Number of lines to capture

    Returns:
        Effect that yields the captured output
    """
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
    """Pause workflow for human input.

    Args:
        session_name: Agent session context for the input
        prompt: Instructions to show the user
        timeout: Optional timeout (None = wait forever)

    Returns:
        Effect that yields the user's input
    """
    return WaitForUserInputEffect(
        session_name=session_name,
        prompt=prompt,
        timeout=timeout,
    )


def StopAgent(session_name: str) -> StopAgentEffect:  # noqa: N802
    """Stop a running agent.

    Args:
        session_name: Agent session to stop

    Returns:
        Effect that yields None
    """
    return StopAgentEffect(session_name=session_name)


# =============================================================================
# Errors
# =============================================================================


class AgenticError(Exception):
    """Base class for agentic workflow errors."""


class WorkflowNotFoundError(AgenticError):
    """Workflow does not exist."""


class AgentNotRunningError(AgenticError):
    """Agent is not currently running."""


class UserInputTimeoutError(AgenticError):
    """Timeout waiting for user input."""


class AmbiguousPrefixError(AgenticError):
    """Workflow ID prefix matches multiple workflows."""


__all__ = [
    # Effect types
    "RunAgentEffect",
    "SendMessageEffect",
    "WaitForStatusEffect",
    "CaptureOutputEffect",
    "WaitForUserInputEffect",
    "StopAgentEffect",
    # Constructors
    "RunAgent",
    "SendMessage",
    "WaitForStatus",
    "CaptureOutput",
    "WaitForUserInput",
    "StopAgent",
    # Errors
    "AgenticError",
    "WorkflowNotFoundError",
    "AgentNotRunningError",
    "UserInputTimeoutError",
    "AmbiguousPrefixError",
]
