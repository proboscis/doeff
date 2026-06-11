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
    def session_node_key(self) -> str:
        """Identity-qualified node key for session naming.

        A session is one execution of (run, node, attempt, RESOLVED
        IDENTITY): the fingerprint digest must enter the name, or a
        profile edit between resumes is invalidated by the journal
        (new generation, correct) but then served the STALE result by
        name-only idempotent re-adoption at L2 — and the stale artifact
        is then re-journaled under the NEW fingerprint, poisoning every
        later replay (observed live, twice). L3 owns this policy; the
        L2 task receives the qualified key as its node id.
        """
        if self.resolved_identity is None:
            return self.node_id
        digest = resolved_identity_fingerprint(self.resolved_identity)[:8]
        return f"{self.node_id}-{digest}"

    @property
    def session_id(self) -> str:
        return deterministic_session_id(
            run_id=self.run_id,
            node_id=self.session_node_key,
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
