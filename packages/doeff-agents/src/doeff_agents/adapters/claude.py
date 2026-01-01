"""Adapter for Claude Code CLI."""

import shutil

from .base import AgentType, InjectionMethod, LaunchConfig


class ClaudeAdapter:
    """Adapter for Claude Code CLI."""

    @property
    def agent_type(self) -> AgentType:
        return AgentType.CLAUDE

    def is_available(self) -> bool:
        return shutil.which("claude") is not None

    def launch_command(self, cfg: LaunchConfig) -> list[str]:
        """Return argv list - caller will shlex.join() if needed."""
        args = ["claude", "--dangerously-skip-permissions"]

        if cfg.profile:
            args.extend(["--profile", cfg.profile])

        if cfg.resume and cfg.session_name:
            args.extend(["--resume", cfg.session_name])

        # Prompt is passed as positional argument (no quoting needed in argv)
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
        return 5  # Claude's status bar area
