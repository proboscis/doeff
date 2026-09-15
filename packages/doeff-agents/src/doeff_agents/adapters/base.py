"""Base protocol and types for agent adapters."""


from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from doeff import Program, do

from doeff_agents.io_effects import which_executable
from doeff_agents.io_root import IoGenerator


@do
def cli_available(name: str) -> IoGenerator[bool]:
    """Program answering whether ``name`` resolves on PATH.

    段 7 lane 7c(決定 1.3): adapter は PATH を自分で引かない — 探索は
    `doeff_agents.io_effects` の要求で、実行は handler(本番 / 検)が持つ。
    """
    found = yield which_executable(name)
    return found is not None


class AgentType(Enum):
    """Supported agent types."""

    CLAUDE = "claude"
    CODEX = "codex"
    GEMINI = "gemini"
    GENERIC = "generic"
    CUSTOM = "custom"


class AgentSessionLifecycle(Enum):
    """How a session should be supervised after an agent turn completes."""

    RUN_TO_COMPLETION = "run_to_completion"
    INTERACTIVE = "interactive"
    #: A warm session (ADR-DOE-AGENTS-012 R10): the turn ends but the session
    #: stays and takes the next one.  The host has spoken this word since that
    #: revision (`policy.hy is-multi-turn`); the client did not, so every warm
    #: row -- which is what the headless backend actually launches -- arrived as
    #: "unparseable" and `ps` skipped it with a warning, leaving `stop`,
    #: `watch` and `output` unable to name the session at all.
    MULTI_TURN = "multi_turn"


class InjectionMethod(Enum):
    """How the prompt should be sent to the agent."""

    # Built-in coding agents must use a live terminal transport. Passing
    # prompts through argv, print/one-shot flags, or stdin starts SDK-style
    # modes and destroys the session that validation retries depend on.
    ARG = "arg"  # Legacy/custom-only escape hatch; forbidden for built-ins.
    STDIN = "stdin"  # Legacy/custom-only escape hatch; forbidden for built-ins.
    TMUX = "tmux"  # Prompt sent to the live terminal after launch.


@dataclass(frozen=True)
class LaunchParams:
    """Parameters for building agent launch command.

    Used only by adapters to build argv — no agent_type needed.
    Adapters must not put ``prompt`` into argv. Session backends send prompts
    after launch through the live terminal transport so agents stay interactive.
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
    mcp_servers: dict[str, str] | None = None


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
    session_env: dict[str, str] | None = None
    lifecycle: AgentSessionLifecycle = AgentSessionLifecycle.RUN_TO_COMPLETION


@runtime_checkable
class PreLaunchAdapter(Protocol):
    """launch の前に自分の家を整える adapter だけが持つ面(Claude の認証の確認)。

    全 adapter が持つ面ではないので `AgentAdapter` とは別の Protocol にする —
    `hasattr` で探ると型が消えるので、isinstance で narrow する。
    """

    def pre_launch(self) -> Program:
        """Program verifying and preparing this agent's own home."""
        ...


class AgentAdapter(Protocol):
    """Protocol for agent adapters."""

    @property
    def agent_type(self) -> AgentType: ...

    def launch_command(self, params: LaunchParams) -> list[str]:
        """Return the command as argv list (NOT a shell string)."""
        ...

    def available(self) -> Program:
        """Program answering whether the agent CLI is installed."""
        ...

    @property
    def injection_method(self) -> InjectionMethod: ...

    @property
    def ready_pattern(self) -> str | None: ...

    @property
    def status_bar_lines(self) -> int: ...
