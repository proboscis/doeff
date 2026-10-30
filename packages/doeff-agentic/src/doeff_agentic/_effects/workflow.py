"""Workflow effects for doeff-agentic."""


from dataclasses import dataclass
from datetime import datetime
from typing import Any

from doeff import EffectBase
from doeff_agentic._types import AgenticWorkflowHandle


@dataclass(frozen=True, kw_only=True)
class AgenticEffectBase(EffectBase):
    """Base class for all doeff-agentic effects."""


@dataclass(frozen=True, kw_only=True)
class AgenticCreateWorkflow(AgenticEffectBase, EffectBase[AgenticWorkflowHandle]):
    """Create a new workflow instance."""

    name: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class AgenticGetWorkflow(AgenticEffectBase, EffectBase[AgenticWorkflowHandle]):
    """Get the current workflow handle."""

    created_at: datetime | None = None
