"""Messaging and event effects for doeff-agentic."""


from dataclasses import dataclass

from doeff import EffectBase
from doeff_agentic._types import (
    AgenticEndOfEvents,
    AgenticEvent,
    AgenticMessage,
    AgenticMessageHandle,
)

from .workflow import AgenticEffectBase


@dataclass(frozen=True, kw_only=True)
class AgenticSendMessage(AgenticEffectBase, EffectBase[AgenticMessageHandle]):
    """Send a message to a session."""

    session_id: str
    content: str
    wait: bool = False
    agent: str | None = None
    model: str | None = None


@dataclass(frozen=True, kw_only=True)
class AgenticGetMessages(AgenticEffectBase, EffectBase[list[AgenticMessage]]):
    """Get messages from a session: answers the session's messages."""

    session_id: str
    limit: int | None = None


@dataclass(frozen=True, kw_only=True)
class AgenticNextEvent(AgenticEffectBase, EffectBase[AgenticEvent | AgenticEndOfEvents]):
    """Wait for the next event from a session."""

    session_id: str
    timeout: float | None = None
