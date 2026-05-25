"""Agent session effects for doeff.

Fine-grained effects for agent session management.

Key design:
- LaunchEffect: flat fields (no LaunchConfig wrapper), user-facing
- ClaudeLaunchEffect: internal, emitted by claude_resolver_handler
- Monitor/Capture/Send/Stop: session lifecycle
- Get/List/Observe/Cleanup/Cancel/Attach: session state management by id
- SessionHandle: immutable value-type identifier
"""
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

    @property
    def session_id(self) -> str:
        """Stable public id for this agent session.

        For the current tmux backend this intentionally equals ``session_name``.
        Other backends can still use the same public API without exposing a
        backend-specific field name to callers.
        """
        return self.session_name


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


@dataclass(frozen=True, kw_only=True)
class AgentSessionSnapshot:
    """Persistent, backend-neutral snapshot of an agent session."""

    session_id: str
    session_name: str
    pane_id: str
    agent_type: AgentType
    work_dir: Path
    status: SessionStatus
    backend_kind: str = "terminal"
    backend_ref: dict[str, str] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_observed_at: datetime | None = None
    finished_at: datetime | None = None
    cleaned_at: datetime | None = None
    pr_url: str | None = None
    output_snippet: str | None = None

    @classmethod
    def from_handle(
        cls,
        handle: SessionHandle,
        *,
        status: SessionStatus,
        backend_kind: str = "terminal",
        backend_ref: dict[str, str] | None = None,
        last_observed_at: datetime | None = None,
        finished_at: datetime | None = None,
        cleaned_at: datetime | None = None,
        pr_url: str | None = None,
        output_snippet: str | None = None,
    ) -> "AgentSessionSnapshot":
        """Create a snapshot from the public handle."""
        return cls(
            session_id=handle.session_id,
            session_name=handle.session_name,
            pane_id=handle.pane_id,
            agent_type=handle.agent_type,
            work_dir=handle.work_dir,
            status=status,
            backend_kind=backend_kind,
            backend_ref=backend_ref
            or {
                "session_name": handle.session_name,
                "pane_id": handle.pane_id,
            },
            started_at=handle.started_at,
            last_observed_at=last_observed_at,
            finished_at=finished_at,
            cleaned_at=cleaned_at,
            pr_url=pr_url,
            output_snippet=output_snippet,
        )

    def to_handle(self) -> SessionHandle:
        """Recreate the public handle from a persisted snapshot."""
        return SessionHandle(
            session_name=self.session_name,
            pane_id=self.pane_id,
            agent_type=self.agent_type,
            work_dir=self.work_dir,
            started_at=self.started_at,
        )

    def with_update(self, **changes: Any) -> "AgentSessionSnapshot":
        """Return a copy with selected fields updated."""
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible values."""
        return {
            "session_id": self.session_id,
            "session_name": self.session_name,
            "pane_id": self.pane_id,
            "agent_type": self.agent_type.value,
            "work_dir": str(self.work_dir),
            "status": self.status.value,
            "backend_kind": self.backend_kind,
            "backend_ref": dict(self.backend_ref),
            "started_at": self.started_at.isoformat(),
            "last_observed_at": (
                self.last_observed_at.isoformat()
                if self.last_observed_at is not None
                else None
            ),
            "finished_at": self.finished_at.isoformat()
            if self.finished_at is not None
            else None,
            "cleaned_at": self.cleaned_at.isoformat()
            if self.cleaned_at is not None
            else None,
            "pr_url": self.pr_url,
            "output_snippet": self.output_snippet,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentSessionSnapshot":
        """Deserialize from JSON-compatible values."""
        return cls(
            session_id=str(data["session_id"]),
            session_name=str(data["session_name"]),
            pane_id=str(data["pane_id"]),
            agent_type=AgentType(str(data["agent_type"])),
            work_dir=Path(str(data["work_dir"])),
            status=SessionStatus(str(data["status"])),
            backend_kind=str(data.get("backend_kind", "terminal")),
            backend_ref=dict(data.get("backend_ref", {})),
            started_at=_parse_datetime(str(data["started_at"])),
            last_observed_at=_parse_optional_datetime(data.get("last_observed_at")),
            finished_at=_parse_optional_datetime(data.get("finished_at")),
            cleaned_at=_parse_optional_datetime(data.get("cleaned_at")),
            pr_url=data.get("pr_url"),
            output_snippet=data.get("output_snippet"),
        )


@dataclass(frozen=True, kw_only=True)
class AgentSessionQuery:
    """Read-only filter for persistent agent session snapshots."""

    status: SessionStatus | None = None
    agent_type: AgentType | None = None
    backend_kind: str | None = None


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _parse_optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value))


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
    mcp_tools: tuple["McpToolDef", ...] = ()
    mcp_server_name: str = "doeff"
    effort: str | None = None
    bare: bool = False
    ready_timeout: float = 30.0
    session_env: dict[str, str] | None = None


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
    mcp_tools: tuple["McpToolDef", ...] = ()
    mcp_server_name: str = "doeff"
    effort: str | None = None
    bare: bool = False
    ready_timeout: float = 30.0
    session_env: dict[str, str] | None = None


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


# =============================================================================
# Session State Effects
# =============================================================================


@dataclass(frozen=True, kw_only=True)
class GetAgentSessionEffect(AgentEffectBase):
    """Read a persisted session snapshot by public session id.

    Yields: AgentSessionSnapshot | None
    """

    session_id: str


@dataclass(frozen=True, kw_only=True)
class ListAgentSessionsEffect(AgentEffectBase):
    """List persisted session snapshots.

    Yields: tuple[AgentSessionSnapshot, ...]
    """

    query: AgentSessionQuery = field(default_factory=AgentSessionQuery)


@dataclass(frozen=True, kw_only=True)
class ObserveAgentSessionEffect(AgentEffectBase):
    """Observe a session by id and persist the resulting snapshot.

    Yields: AgentSessionSnapshot
    """

    session_id: str
    lines: int = 100


@dataclass(frozen=True, kw_only=True)
class AttachAgentSessionEffect(AgentEffectBase):
    """Attach to a session by id using the active backend.

    Yields: None
    """

    session_id: str


@dataclass(frozen=True, kw_only=True)
class CancelAgentSessionEffect(AgentEffectBase):
    """Cancel a running session by id and persist the resulting status.

    Yields: AgentSessionSnapshot
    """

    session_id: str


@dataclass(frozen=True, kw_only=True)
class CleanupAgentSessionEffect(AgentEffectBase):
    """Clean up backend resources for a session by id.

    Yields: AgentSessionSnapshot
    """

    session_id: str


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
    mcp_tools: tuple["McpToolDef", ...] = (),
    mcp_server_name: str = "doeff",
    effort: str | None = None,
    bare: bool = False,
    ready_timeout: float = 30.0,
    session_env: dict[str, str] | None = None,
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
        effort=effort,
        bare=bare,
        ready_timeout=ready_timeout,
        session_env=session_env,
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


def GetAgentSession(session_id: str) -> GetAgentSessionEffect:  # noqa: N802
    return GetAgentSessionEffect(session_id=session_id)


def ListAgentSessions(  # noqa: N802
    *,
    status: SessionStatus | None = None,
    agent_type: AgentType | None = None,
    backend_kind: str | None = None,
) -> ListAgentSessionsEffect:
    return ListAgentSessionsEffect(
        query=AgentSessionQuery(
            status=status,
            agent_type=agent_type,
            backend_kind=backend_kind,
        )
    )


def ObserveAgentSession(  # noqa: N802
    session_id: str,
    *,
    lines: int = 100,
) -> ObserveAgentSessionEffect:
    return ObserveAgentSessionEffect(session_id=session_id, lines=lines)


def AttachAgentSession(session_id: str) -> AttachAgentSessionEffect:  # noqa: N802
    return AttachAgentSessionEffect(session_id=session_id)


def CancelAgentSession(session_id: str) -> CancelAgentSessionEffect:  # noqa: N802
    return CancelAgentSessionEffect(session_id=session_id)


def CleanupAgentSession(session_id: str) -> CleanupAgentSessionEffect:  # noqa: N802
    return CleanupAgentSessionEffect(session_id=session_id)


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
    "AgentError",
    "AgentLaunchError",
    "AgentNotAvailableError",
    "AgentReadyTimeoutError",
    "AgentSessionQuery",
    "AgentSessionSnapshot",
    "AttachAgentSession",
    "AttachAgentSessionEffect",
    "CancelAgentSession",
    "CancelAgentSessionEffect",
    "Capture",
    "CaptureEffect",
    "ClaudeLaunchEffect",
    "CleanupAgentSession",
    "CleanupAgentSessionEffect",
    "GetAgentSession",
    "GetAgentSessionEffect",
    "Launch",
    "LaunchEffect",
    "ListAgentSessions",
    "ListAgentSessionsEffect",
    "Monitor",
    "MonitorEffect",
    "Observation",
    "ObserveAgentSession",
    "ObserveAgentSessionEffect",
    "Send",
    "SendEffect",
    "SessionAlreadyExistsError",
    "SessionHandle",
    "SessionNotFoundError",
    "Stop",
    "StopEffect",
]
