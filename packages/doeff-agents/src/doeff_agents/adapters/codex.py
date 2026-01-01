"""Adapter for OpenAI Codex CLI."""

import shutil

from .base import AgentType, InjectionMethod, LaunchConfig


class CodexAdapter:
    """Adapter for OpenAI Codex CLI."""

    @property
    def agent_type(self) -> AgentType:
        return AgentType.CODEX

    def is_available(self) -> bool:
        return shutil.which("codex") is not None

    def launch_command(self, cfg: LaunchConfig) -> list[str]:
        """Return argv list - caller will shlex.join() if needed."""
        args = ["codex", "--full-auto"]

        if cfg.profile:
            args.extend(["--profile", cfg.profile])

        # Prompt is passed as positional argument
        if cfg.prompt:
            args.append(cfg.prompt)

        return args

    @property
    def injection_method(self) -> InjectionMethod:
        return InjectionMethod.ARG

    @property
    def ready_pattern(self) -> str | None:
        return None  # Prompt passed via command line

    @property
    def status_bar_lines(self) -> int:
        return 3  # Codex's status bar area
