"""Adapter for Gemini CLI."""

from doeff import Program

from .base import AgentType, InjectionMethod, LaunchParams, cli_available


class GeminiAdapter:
    """Adapter for Gemini CLI.

    Gemini uses interactive prompt injection via tmux send-keys
    after the agent starts up.
    """

    @property
    def agent_type(self) -> AgentType:
        return AgentType.GEMINI

    def available(self) -> Program:
        """Program answering whether the Gemini CLI is on PATH."""
        return cli_available("gemini")

    def launch_command(self, params: LaunchParams) -> list[str]:
        """Return argv list - caller will shlex.join() if needed."""
        args = ["gemini"]
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
