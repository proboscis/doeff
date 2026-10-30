"""Status and capability effects for doeff-agentic."""


from dataclasses import dataclass

from doeff import EffectBase
from doeff_agentic._types import AgenticSessionStatus

from .workflow import AgenticEffectBase


@dataclass(frozen=True, kw_only=True)
class AgenticGetSessionStatus(AgenticEffectBase, EffectBase[AgenticSessionStatus]):
    """Get current status for a session."""

    session_id: str


@dataclass(frozen=True, kw_only=True)
class AgenticSupportsCapability(AgenticEffectBase, EffectBase[bool]):
    """Check whether a handler supports a capability."""

    capability: str
