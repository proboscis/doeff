"""Workflow effects for doeff-agentic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class AgenticEffectBase(EffectBase):
    """Base class for all doeff-agentic effects."""


@dataclass(frozen=True, kw_only=True)
class AgenticCreateWorkflow(AgenticEffectBase):
    """Create a new workflow instance."""

    name: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class AgenticGetWorkflow(AgenticEffectBase):
    """Get the current workflow handle."""
