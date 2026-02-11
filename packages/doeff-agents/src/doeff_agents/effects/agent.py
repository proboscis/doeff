"""Agent session effects for doeff.

This module provides fine-grained effects for agent session management,
designed to be immutable and composable.

Key design principles:
- Effects hold immutable SessionHandle, not mutable AgentSession
- Fine-grained effects for core operations (Launch, Monitor, Capture, Send, Stop, Sleep)
- High-level convenience via Program composition, not monolithic effects
- Sleep effect for testable polling
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from doeff import EffectBase
from doeff_agents.adapters.base import AgentType, LaunchConfig
from doeff_agents.monitor import SessionStatus

# =============================================================================
# SessionHandle - Immutable session identifier
# =============================================================================


@dataclass(frozen=True)
class SessionHandle:
    """Immutable handle to an agent session.

    This is a value type that identifies a session without holding mutable state.
    All session state is managed by the effect handler.
    """

    session_name: str
    pane_id: str
    agent_type: AgentType
    work_dir: Path
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"SessionHandle({self.session_name!r}, pane={self.pane_id})"


# =============================================================================
# Observation - Immutable snapshot of session state
# =============================================================================


@dataclass(frozen=True)
class Observation:
    """Immutable snapshot of session state from monitoring.

    Contains the current status and optional details about the session.
    """

    status: SessionStatus
    output_changed: bool = False
    pr_url: str | None = None
    output_snippet: str | None = None

    @property
    def is_terminal(self) -> bool:
        """Check if this observation indicates a terminal state."""
        return self.status in (
            SessionStatus.DONE,
            SessionStatus.FAILED,
            SessionStatus.EXITED,
            SessionStatus.STOPPED,
        )


# =============================================================================
# Effect Base
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class AgentEffectBase(EffectBase):
    """Base class for agent effects.

    Inherits from doeff's public EffectBase type.
    """



# =============================================================================
# Fine-grained Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class LaunchEffect(AgentEffectBase):
    """Effect to launch a new agent session.

    Yields: SessionHandle
    """

    session_name: str
    config: LaunchConfig
    ready_timeout: float = 30.0


@dataclass(frozen=True, kw_only=True)
class MonitorEffect(AgentEffectBase):
    """Effect to check and update session status.

    This is a single poll - for continuous monitoring, use Sleep in a loop.

    Yields: Observation
    """

    handle: SessionHandle


@dataclass(frozen=True, kw_only=True)
class CaptureEffect(AgentEffectBase):
    """Effect to capture current pane output.

    Yields: str (the captured output)
    """

    handle: SessionHandle
    lines: int = 100


@dataclass(frozen=True, kw_only=True)
class SendEffect(AgentEffectBase):
    """Effect to send a message or keys to the session.

    Yields: None
    """

    handle: SessionHandle
    message: str
    enter: bool = True
    literal: bool = True  # If False, interprets special keys like C-c


@dataclass(frozen=True, kw_only=True)
class StopEffect(AgentEffectBase):
    """Effect to stop (kill) an agent session.

    Yields: None
    """

    handle: SessionHandle


@dataclass(frozen=True, kw_only=True)
class SleepEffect(AgentEffectBase):
    """Effect to sleep for a duration.

    Using Sleep as an effect makes polling testable - mock handlers
    can advance time instantly.

    Yields: None
    """

    seconds: float


# =============================================================================
# Bracket Effect for Resource Management
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class WithSessionEffect(AgentEffectBase):
    """Bracket effect for session lifecycle.

    Ensures session is stopped even if inner program fails.
    This is the agent equivalent of `bracket` or `try-finally`.

    The handler is responsible for:
    1. Running acquire (launch session)
    2. Running use with the handle
    3. Always running release (stop session)

    Yields: Result of inner program
    """

    session_name: str
    config: LaunchConfig
    ready_timeout: float = 30.0
    # Note: The `use` function is passed at the Program level, not here
    # This effect just marks the boundary for the handler


# =============================================================================
# Effect Constructors (with creation context tracking)
# =============================================================================


def _create_effect(effect: AgentEffectBase, skip_frames: int = 2) -> AgentEffectBase:
    """Create effect with creation context for better error messages.

    We avoid importing doeff.utils to keep this module self-contained.
    The handler can add context if needed.
    """
    # For now, return as-is. The full doeff integration will add tracing.
    return effect


def Launch(  # noqa: N802 - PascalCase follows doeff convention for effect constructors
    session_name: str,
    config: LaunchConfig,
    *,
    ready_timeout: float = 30.0,
) -> LaunchEffect:
    """Create a Launch effect."""
    return LaunchEffect(
        session_name=session_name,
        config=config,
        ready_timeout=ready_timeout,
    )


def Monitor(handle: SessionHandle) -> MonitorEffect:  # noqa: N802
    """Create a Monitor effect."""
    return MonitorEffect(handle=handle)


def Capture(handle: SessionHandle, *, lines: int = 100) -> CaptureEffect:  # noqa: N802
    """Create a Capture effect."""
    return CaptureEffect(handle=handle, lines=lines)


def Send(  # noqa: N802
    handle: SessionHandle,
    message: str,
    *,
    enter: bool = True,
    literal: bool = True,
) -> SendEffect:
    """Create a Send effect."""
    return SendEffect(handle=handle, message=message, enter=enter, literal=literal)


def Stop(handle: SessionHandle) -> StopEffect:  # noqa: N802
    """Create a Stop effect."""
    return StopEffect(handle=handle)


def Sleep(seconds: float) -> SleepEffect:  # noqa: N802
    """Create a Sleep effect."""
    return SleepEffect(seconds=seconds)


# =============================================================================
# Errors
# =============================================================================


class AgentError(Exception):
    """Base class for agent-related errors."""


class AgentLaunchError(AgentError):
    """Error during agent launch."""


class AgentNotAvailableError(AgentLaunchError):
    """Agent CLI is not available."""


class AgentReadyTimeoutError(AgentLaunchError):
    """Agent did not become ready within timeout."""


class SessionNotFoundError(AgentError):
    """Session does not exist."""


class SessionAlreadyExistsError(AgentError):
    """Session already exists."""


__all__ = [  # noqa: RUF022 - grouped by category for readability
    # Types
    "SessionHandle",
    "Observation",
    # Effects
    "LaunchEffect",
    "MonitorEffect",
    "CaptureEffect",
    "SendEffect",
    "StopEffect",
    "SleepEffect",
    "WithSessionEffect",
    # Constructors
    "Launch",
    "Monitor",
    "Capture",
    "Send",
    "Stop",
    "Sleep",
    # Errors
    "AgentError",
    "AgentLaunchError",
    "AgentNotAvailableError",
    "AgentReadyTimeoutError",
    "SessionNotFoundError",
    "SessionAlreadyExistsError",
]
