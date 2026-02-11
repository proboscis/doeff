"""Messaging and event effects for doeff-agentic."""

from __future__ import annotations

from dataclasses import dataclass

from .workflow import AgenticEffectBase


@dataclass(frozen=True, kw_only=True)
class AgenticSendMessage(AgenticEffectBase):
    """Send a message to a session."""

    session_id: str
    content: str
    wait: bool = False
    agent: str | None = None
    model: str | None = None


@dataclass(frozen=True, kw_only=True)
class AgenticGetMessages(AgenticEffectBase):
    """Get messages from a session."""

    session_id: str
    limit: int | None = None


@dataclass(frozen=True, kw_only=True)
class AgenticNextEvent(AgenticEffectBase):
    """Wait for the next event from a session."""

    session_id: str
    timeout: float | None = None
