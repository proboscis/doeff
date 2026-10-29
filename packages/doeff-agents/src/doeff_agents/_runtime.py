"""Runtime policy for agent launch effects."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, kw_only=True)
class ClaudeRuntimePolicy:
    """Runtime-owned Claude selection and environment policy."""

    model: str | None = None
    agent_home: Path | None = None
    trusted_workspaces: tuple[Path, ...] = ()
    bootstrap_exports: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class CodexRuntimePolicy:
    """Runtime-owned codex auth/profile policy (ADR-DOE-AGENTS-004 R9).

    Auth material belongs to the handler BINDER, not to the effect user:
    the launch surface is auth-blind and session_env is a non-auth overlay
    (binding-owned keys are rejected).  For in-process (local) bindings
    this dataclass is the constructor-injection home for CODEX_HOME —
    the local mirror of the wire's typed ``binding`` field.  When unset,
    handlers fall back to the binder process environment (the daemon's
    own env is binder configuration too), never to the effect.
    """

    codex_home: Path | None = None

