"""Environment effects for doeff-agentic."""


from dataclasses import dataclass

from doeff_agentic.types import AgenticEnvironmentType

from .workflow import AgenticEffectBase


@dataclass(frozen=True, kw_only=True)
class AgenticCreateEnvironment(AgenticEffectBase):
    """Create a new environment for agent sessions."""

    env_type: AgenticEnvironmentType
    name: str | None = None
    base_commit: str | None = None
    source_environment_id: str | None = None
    working_dir: str | None = None


@dataclass(frozen=True, kw_only=True)
class AgenticGetEnvironment(AgenticEffectBase):
    """Get an existing environment by ID."""

    environment_id: str


@dataclass(frozen=True, kw_only=True)
class AgenticDeleteEnvironment(AgenticEffectBase):
    """Delete an existing environment by ID."""

    environment_id: str
    force: bool = False
