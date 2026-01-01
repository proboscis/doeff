"""Adapter for Gemini CLI."""

import shutil

from .base import AgentType, InjectionMethod, LaunchConfig


class GeminiAdapter:
    """Adapter for Gemini CLI.

    Gemini uses interactive prompt injection via tmux send-keys
    after the agent starts up.
    """

    @property
    def agent_type(self) -> AgentType:
        return AgentType.GEMINI

    def is_available(self) -> bool:
        return shutil.which("gemini") is not None

    def launch_command(self, cfg: LaunchConfig) -> list[str]:
        """Return argv list - caller will shlex.join() if needed."""
        args = ["gemini"]

        if cfg.profile:
            args.extend(["--profile", cfg.profile])

        # Gemini launches interactively - prompt is sent via tmux
        return args

    @property
    def injection_method(self) -> InjectionMethod:
        return InjectionMethod.TMUX

    @property
    def ready_pattern(self) -> str | None:
        # Pattern to detect Gemini is ready for input
        return r"Type your message|Enter your prompt|>"

    @property
    def status_bar_lines(self) -> int:
        return 3  # Gemini's status bar area
