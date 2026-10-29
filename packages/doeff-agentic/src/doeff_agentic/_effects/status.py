"""Status and capability effects for doeff-agentic."""


from dataclasses import dataclass

from .workflow import AgenticEffectBase


@dataclass(frozen=True, kw_only=True)
class AgenticGetSessionStatus(AgenticEffectBase):
    """Get current status for a session."""

    session_id: str


@dataclass(frozen=True, kw_only=True)
class AgenticSupportsCapability(AgenticEffectBase):
    """Check whether a handler supports a capability."""

    capability: str
