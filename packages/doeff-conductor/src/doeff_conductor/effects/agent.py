"""
Agent effects for doeff-conductor.

Workflow-facing agent effects return schema-validated artifacts. Interactive
session controls live below this boundary and are not conductor workflow effects.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from doeff_agents.effects import (  # re-exported for conductor callers
    AgentAttemptExhaustedError,
    AgentValidationErrorKind,
    AgentValidationFailure,
    deterministic_session_id,
)

from .base import ConductorEffectBase

if TYPE_CHECKING:
    from ..types import Workspace


@dataclass(frozen=True, kw_only=True)
class AgentTask:
    """Schema-validated conductor worker task."""

    run_id: str
    node_id: str
    attempt: int
    env: "Workspace"
    prompt: str
    result_schema: dict[str, Any]
    verification_class: str
    agent_type: str
    name: str | None = None
    profile: str | None = None
    model: str | None = None
    effort: str | None = None
    max_retries: int = 2
    timeout_seconds: float | None = None

    @property
    def session_id(self) -> str:
        return deterministic_session_id(
            run_id=self.run_id,
            node_id=self.node_id,
            attempt=self.attempt,
        )


@dataclass(frozen=True)
class AgentEffect(ConductorEffectBase):
    """Run an agent and return its validated artifact object."""

    task: AgentTask


def Agent(task: AgentTask) -> AgentEffect:  # noqa: N802
    return AgentEffect(task=task)


__all__ = [
    "Agent",
    "AgentAttemptExhaustedError",
    "AgentEffect",
    "AgentTask",
    "AgentValidationErrorKind",
    "AgentValidationFailure",
]
