"""Session effects for doeff-agentic."""


from dataclasses import dataclass

from doeff import EffectBase
from doeff_agentic._types import AgenticSessionHandle

from .workflow import AgenticEffectBase


@dataclass(frozen=True, kw_only=True)
class AgenticCreateSession(AgenticEffectBase, EffectBase[AgenticSessionHandle]):
    """Create a new agent session."""

    name: str
    environment_id: str | None = None
    title: str | None = None
    agent: str | None = None
    model: str | None = None


@dataclass(frozen=True, kw_only=True)
class AgenticForkSession(AgenticEffectBase, EffectBase[AgenticSessionHandle]):
    """Fork an existing session."""

    session_id: str
    name: str
    message_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class AgenticGetSession(AgenticEffectBase, EffectBase[AgenticSessionHandle]):
    """Get an existing session by ID or name."""

    session_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, kw_only=True)
class AgenticAbortSession(AgenticEffectBase, EffectBase[None]):
    """Abort a running session."""

    session_id: str


@dataclass(frozen=True, kw_only=True)
class AgenticDeleteSession(AgenticEffectBase, EffectBase[bool]):
    """Delete an existing session."""

    session_id: str
