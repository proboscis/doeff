"""Effect definitions for doeff-agentic."""


from dataclasses import dataclass
from typing import Any

from doeff_agentic.exceptions import (
    AgenticAmbiguousPrefixError,
    AgenticDuplicateNameError,
    AgenticEnvironmentInUseError,
    AgenticEnvironmentNotFoundError,
    AgenticError,
    AgenticServerError,
    AgenticSessionNotFoundError,
    AgenticSessionNotRunningError,
    AgenticTimeoutError,
    AgenticUnsupportedOperationError,
    AgenticWorkflowNotFoundError,
)
from doeff_agentic.types import AgentConfig, AgentStatus

from .environment import (
    AgenticCreateEnvironment,
    AgenticDeleteEnvironment,
    AgenticGetEnvironment,
)
from .messaging import (
    AgenticGetMessages,
    AgenticNextEvent,
    AgenticSendMessage,
)
from .session import (
    AgenticAbortSession,
    AgenticCreateSession,
    AgenticDeleteSession,
    AgenticForkSession,
    AgenticGetSession,
)
from .status import (
    AgenticGetSessionStatus,
    AgenticSupportsCapability,
)
from .workflow import (
    AgenticCreateWorkflow,
    AgenticEffectBase,
    AgenticGetWorkflow,
)

# =============================================================================
# Legacy Effects (for backward compatibility)
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class RunAgentEffect(AgenticEffectBase):
    """Effect to run an agent to completion (deprecated)."""

    config: AgentConfig
    session_name: str | None = None
    poll_interval: float = 1.0
    ready_timeout: float = 30.0


@dataclass(frozen=True, kw_only=True)
class SendMessageEffect(AgenticEffectBase):
    """Effect to send a message (deprecated)."""

    session_name: str
    message: str
    enter: bool = True


@dataclass(frozen=True, kw_only=True)
class WaitForStatusEffect(AgenticEffectBase):
    """Effect to wait for status (deprecated)."""

    session_name: str
    target_status: AgentStatus | tuple[AgentStatus, ...]
    timeout: float = 300.0
    poll_interval: float = 1.0


@dataclass(frozen=True, kw_only=True)
class CaptureOutputEffect(AgenticEffectBase):
    """Effect to capture output (deprecated)."""

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
    """Effect to stop an agent (deprecated)."""

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
    """Stop an agent (deprecated)."""
    return StopAgentEffect(session_name=session_name)


# Legacy error aliases
WorkflowNotFoundError = AgenticWorkflowNotFoundError
AgentNotRunningError = AgenticSessionNotRunningError
UserInputTimeoutError = AgenticTimeoutError
AmbiguousPrefixError = AgenticAmbiguousPrefixError


__all__ = [
    "AgentNotRunningError",
    "AgenticAbortSession",
    "AgenticAmbiguousPrefixError",
    "AgenticCreateEnvironment",
    "AgenticCreateSession",
    "AgenticCreateWorkflow",
    "AgenticDeleteEnvironment",
    "AgenticDeleteSession",
    "AgenticDuplicateNameError",
    "AgenticEffectBase",
    "AgenticEnvironmentInUseError",
    "AgenticEnvironmentNotFoundError",
    "AgenticError",
    "AgenticForkSession",
    "AgenticGetEnvironment",
    "AgenticGetMessages",
    "AgenticGetSession",
    "AgenticGetSessionStatus",
    "AgenticGetWorkflow",
    "AgenticNextEvent",
    "AgenticSendMessage",
    "AgenticServerError",
    "AgenticSessionNotFoundError",
    "AgenticSessionNotRunningError",
    "AgenticSupportsCapability",
    "AgenticTimeoutError",
    "AgenticUnsupportedOperationError",
    "AgenticWorkflowNotFoundError",
    "AmbiguousPrefixError",
    "CaptureOutput",
    "CaptureOutputEffect",
    "RunAgent",
    "RunAgentEffect",
    "SendMessage",
    "SendMessageEffect",
    "StopAgent",
    "StopAgentEffect",
    "UserInputTimeoutError",
    "WaitForStatus",
    "WaitForStatusEffect",
    "WaitForUserInput",
    "WaitForUserInputEffect",
    "WorkflowNotFoundError",
]
