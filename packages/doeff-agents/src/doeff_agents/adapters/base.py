"""Base protocol and types for agent adapters."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol


class AgentType(Enum):
    """Supported agent types."""

    CLAUDE = "claude"
    CODEX = "codex"
    GEMINI = "gemini"
    CUSTOM = "custom"


class InjectionMethod(Enum):
    """How the prompt should be sent to the agent."""

    ARG = "arg"  # Prompt passed as command-line argument
    TMUX = "tmux"  # Prompt sent via tmux send-keys after launch


@dataclass(frozen=True)
class LaunchConfig:
    """Configuration for launching an agent.

    Note: prompt is optional for resume/interactive sessions.
    """

    agent_type: AgentType
    work_dir: Path
    prompt: str | None = None  # Optional for resume/interactive sessions
    resume: bool = False
    session_name: str | None = None  # For resume
    profile: str | None = None  # For agents that support profiles


@dataclass(frozen=True)
class CustomLaunchConfig(LaunchConfig):
    """Extended config for custom agents."""

    custom_cmd: str | None = None
    custom_args: list[str] = field(default_factory=list)


class AgentAdapter(Protocol):
    """Protocol for agent adapters."""

    @property
    def agent_type(self) -> AgentType:
        """Return the agent type."""
        ...

    def launch_command(self, cfg: LaunchConfig) -> list[str]:
        """Return the command as argv list (NOT a shell string).

        Returns argv list to avoid shell injection and quoting issues.
        The caller will use shlex.join() if a shell string is needed.
        """
        ...

    def is_available(self) -> bool:
        """Check if the agent CLI is installed."""
        ...

    @property
    def injection_method(self) -> InjectionMethod:
        """How the prompt should be sent to the agent."""
        ...

    @property
    def ready_pattern(self) -> str | None:
        """Regex pattern to detect when agent is ready for input.

        Used when injection_method is TMUX.
        Return None if not needed.
        """
        ...

    @property
    def status_bar_lines(self) -> int:
        """Number of lines to skip when hashing content (status bar).

        Default is 5 for Claude. Override for other agents.
        """
        ...
