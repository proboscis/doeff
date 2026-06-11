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

from doeff_conductor.effects.base import ConductorEffectBase
from doeff_conductor.replay_keying import ResolvedIdentity, resolved_identity_fingerprint

if TYPE_CHECKING:
    from doeff_conductor.types import Workspace


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
    resolved_identity: ResolvedIdentity | None = None
    max_retries: int = 2
    timeout_seconds: float | None = None

    @property
    def session_id(self) -> str:
        # A session is one execution of (run, node, attempt, RESOLVED
        # IDENTITY): the fingerprint digest must enter the name, or a
        # profile edit between resumes is invalidated by the journal
        # (new generation, correct) but then served the STALE result by
        # name-only idempotent re-adoption at L2 (observed live: an
        # effort change re-dispatched every agent and got the old
        # sessions' payloads back in seconds, defeating D7 end to end).
        node_key = self.node_id
        if self.resolved_identity is not None:
            digest = resolved_identity_fingerprint(self.resolved_identity)[:8]
            node_key = f"{self.node_id}-{digest}"
        return deterministic_session_id(
            run_id=self.run_id,
            node_id=node_key,
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
