"""Base protocol and types for agent adapters."""

from __future__ import annotations

from dataclasses import dataclass
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
class LaunchParams:
    """Parameters for building agent launch command.

    Used only by adapters to build argv — no agent_type needed.
    """

    work_dir: Path
    prompt: str | None = None
    model: str | None = None
    # Claude-specific knobs (adapters that don't use them ignore them silently).
    # `effort` maps to --effort; default None leaves Claude Code's default (xhigh).
    # `bare` maps to --bare; when True, Claude Code skips hooks, LSP, plugin
    # sync, auto-memory, and CLAUDE.md auto-discovery — significantly reduces
    # startup time and per-turn prompt size for focused execution tasks.
    effort: str | None = None
    bare: bool = False


@dataclass(frozen=True)
class LaunchConfig:
    """Configuration for the imperative session API (session.py).

    This is the old-style config that includes agent_type. New code should
    use LaunchEffect (effects API) which has flat fields on the effect itself.
    Kept for backward compat with session.py, programs.py, and CLI.
    """

    agent_type: AgentType
    work_dir: Path
    prompt: str | None = None
    model: str | None = None
    mcp_tools: tuple = ()


class AgentAdapter(Protocol):
    """Protocol for agent adapters."""

    @property
    def agent_type(self) -> AgentType: ...

    def launch_command(self, params: LaunchParams) -> list[str]:
        """Return the command as argv list (NOT a shell string)."""
        ...

    def is_available(self) -> bool:
        """Check if the agent CLI is installed."""
        ...

    @property
    def injection_method(self) -> InjectionMethod: ...

    @property
    def ready_pattern(self) -> str | None: ...

    @property
    def status_bar_lines(self) -> int: ...
