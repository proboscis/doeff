"""Agent session effects for doeff.

Fine-grained effects for agent session management.

Key design:
- LaunchEffect: flat fields (no LaunchConfig wrapper), user-facing
- ClaudeLaunchEffect: internal, emitted by claude_resolver_handler
- Monitor/Capture/Send/Stop/Sleep: session lifecycle
- SessionHandle: immutable value-type identifier
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from doeff import EffectBase

if TYPE_CHECKING:
    from doeff.mcp import McpToolDef

from doeff_agents.adapters.base import AgentType
from doeff_agents.monitor import SessionStatus

# =============================================================================
# SessionHandle - Immutable session identifier
# =============================================================================


@dataclass(frozen=True)
class SessionHandle:
    """Immutable handle to an agent session.

    Value type — identifies a session without holding mutable state.
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
    """Immutable snapshot of session state from monitoring."""

    status: SessionStatus
    output_changed: bool = False
    pr_url: str | None = None
    output_snippet: str | None = None

    @property
    def is_terminal(self) -> bool:
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
    """Base class for agent effects."""


# =============================================================================
# Launch Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class LaunchEffect(AgentEffectBase):
    """Launch a new agent session.

    User-facing effect — flat fields, no config wrapper.
    The claude_resolver_handler converts this to ClaudeLaunchEffect
    when agent_type is CLAUDE.

    Yields: SessionHandle
    """

    session_name: str
    agent_type: AgentType
    work_dir: Path
    prompt: str | None = None
    model: str | None = None
    mcp_tools: tuple[McpToolDef, ...] = ()
    mcp_server_name: str = "doeff"
    ready_timeout: float = 30.0


@dataclass(frozen=True, kw_only=True)
class ClaudeLaunchEffect(AgentEffectBase):
    """Claude-specific launch — internal effect (handler-to-handler).

    Emitted by claude_resolver_handler, handled by claude_handler.
    Trust setup, MCP server, tmux, onboarding are handler-internal.

    Yields: SessionHandle
    """

    session_name: str
    work_dir: Path
    prompt: str | None = None
    model: str | None = None
    mcp_tools: tuple[McpToolDef, ...] = ()
    mcp_server_name: str = "doeff"
    ready_timeout: float = 30.0


# =============================================================================
# Session Lifecycle Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class MonitorEffect(AgentEffectBase):
    """Check and update session status (single poll).

    Yields: Observation
    """

    handle: SessionHandle


@dataclass(frozen=True, kw_only=True)
class CaptureEffect(AgentEffectBase):
    """Capture current pane output.

    Yields: str
    """

    handle: SessionHandle
    lines: int = 100


@dataclass(frozen=True, kw_only=True)
class SendEffect(AgentEffectBase):
    """Send a message or keys to the session.

    Yields: None
    """

    handle: SessionHandle
    message: str
    enter: bool = True
    literal: bool = True


@dataclass(frozen=True, kw_only=True)
class StopEffect(AgentEffectBase):
    """Stop (kill) an agent session.

    Yields: None
    """

    handle: SessionHandle


@dataclass(frozen=True, kw_only=True)
class SleepEffect(AgentEffectBase):
    """Sleep for a duration (testable — mock handlers skip the wait).

    Yields: None
    """

    seconds: float


# =============================================================================
# Effect Constructors
# =============================================================================


# =============================================================================
# Deprecated — kept temporarily for backward compatibility during migration
# =============================================================================

# These will be removed once handlers/production.py and handlers/testing.py
# are rewritten as Hy defhandlers.

@dataclass(frozen=True, kw_only=True)
class _DeprecatedLaunchTaskEffect(AgentEffectBase):
    """DEPRECATED: Use LaunchEffect directly. Will be removed."""
    session_name: str
    # Stub — just enough for old code to import without crashing

LaunchTaskEffect = _DeprecatedLaunchTaskEffect  # backward compat alias


# =============================================================================
# Effect Constructors
# =============================================================================


def Launch(  # noqa: N802
    session_name: str,
    *,
    agent_type: AgentType,
    work_dir: Path,
    prompt: str | None = None,
    model: str | None = None,
    mcp_tools: tuple[McpToolDef, ...] = (),
    mcp_server_name: str = "doeff",
    ready_timeout: float = 30.0,
) -> LaunchEffect:
    """Create a Launch effect with flat fields."""
    return LaunchEffect(
        session_name=session_name,
        agent_type=agent_type,
        work_dir=work_dir,
        prompt=prompt,
        model=model,
        mcp_tools=mcp_tools,
        mcp_server_name=mcp_server_name,
        ready_timeout=ready_timeout,
    )


def Monitor(handle: SessionHandle) -> MonitorEffect:  # noqa: N802
    return MonitorEffect(handle=handle)


def Capture(handle: SessionHandle, *, lines: int = 100) -> CaptureEffect:  # noqa: N802
    return CaptureEffect(handle=handle, lines=lines)


def Send(  # noqa: N802
    handle: SessionHandle,
    message: str,
    *,
    enter: bool = True,
    literal: bool = True,
) -> SendEffect:
    return SendEffect(handle=handle, message=message, enter=enter, literal=literal)


def Stop(handle: SessionHandle) -> StopEffect:  # noqa: N802
    return StopEffect(handle=handle)


def Sleep(seconds: float) -> SleepEffect:  # noqa: N802
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


__all__ = [
    "SessionHandle",
    "Observation",
    "LaunchEffect",
    "ClaudeLaunchEffect",
    "MonitorEffect",
    "CaptureEffect",
    "SendEffect",
    "StopEffect",
    "SleepEffect",
    "Launch",
    "Monitor",
    "Capture",
    "Send",
    "Stop",
    "Sleep",
    "AgentError",
    "AgentLaunchError",
    "AgentNotAvailableError",
    "AgentReadyTimeoutError",
    "SessionNotFoundError",
    "SessionAlreadyExistsError",
]
