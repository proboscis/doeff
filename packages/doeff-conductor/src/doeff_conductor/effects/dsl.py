"""L3 workflow DSL effects for doeff-conductor."""

from dataclasses import dataclass, field
from typing import Any

from doeff_conductor.effects.base import ConductorEffectBase


@dataclass(frozen=True, kw_only=True)
class WorkspaceCall(ConductorEffectBase):
    """Create or resolve a workflow workspace value."""

    repo: str | None = None
    from_ref: Any | None = None


@dataclass(frozen=True, kw_only=True)
class MergeCall(ConductorEffectBase):
    """Deterministically reconcile workspace values."""

    workspaces: tuple[Any, ...]
    strategy: str = "merge"


@dataclass(frozen=True, kw_only=True)
class AgentCall(ConductorEffectBase):
    """Schema-enforced worker call emitted by ``agent!``."""

    role: str
    verification_class: str
    prompt: Any
    schema: Any
    workspace: Any | None = None
    files: frozenset[str] = field(default_factory=frozenset)
    profile: str | None = None
    persona: str | None = None
    retry: int | None = None
    # Wall-clock deadline declared on the node spec (L-K4-3); the L3
    # runtime observes it and parks a K5 gate on exceed. Never a
    # transport timeout.
    deadline_seconds: float | None = None
    label: str | None = None
    phase: str | None = None


@dataclass(frozen=True, kw_only=True)
class GateCall(ConductorEffectBase):
    """Deterministic gate emitted by ``gate!``."""

    cmd: str
    workspace: Any | None = None
    timeout: int | None = None
    phase: str | None = None


@dataclass(frozen=True, kw_only=True)
class TimeCall(ConductorEffectBase):
    """Explicit clock effect emitted by ``time!``."""

    label: str | None = None
    run_id: str | None = None
    node_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class RandomCall(ConductorEffectBase):
    """Explicit randomness effect emitted by ``random!``."""

    spec: Any
    label: str | None = None
    run_id: str | None = None
    node_id: str | None = None
