"""Session effects for doeff-agentic."""

from __future__ import annotations

from dataclasses import dataclass

from .workflow import AgenticEffectBase


@dataclass(frozen=True, kw_only=True)
class AgenticCreateSession(AgenticEffectBase):
    """Create a new agent session."""

    name: str
    environment_id: str | None = None
    title: str | None = None
    agent: str | None = None
    model: str | None = None


@dataclass(frozen=True, kw_only=True)
class AgenticForkSession(AgenticEffectBase):
    """Fork an existing session."""

    session_id: str
    name: str
    message_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class AgenticGetSession(AgenticEffectBase):
    """Get an existing session by ID or name."""

    session_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, kw_only=True)
class AgenticAbortSession(AgenticEffectBase):
    """Abort a running session."""

    session_id: str


@dataclass(frozen=True, kw_only=True)
class AgenticDeleteSession(AgenticEffectBase):
    """Delete an existing session."""

    session_id: str
