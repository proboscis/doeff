#!/usr/bin/env python3
"""
Custom Adapter Example

This example shows how to create a custom adapter for an agent that isn't
built into doeff-agents. The adapter pattern allows you to integrate any
command-line agent that runs in a terminal.

Key concepts:
- Implementing the AgentAdapter protocol
- Registering custom adapters
- Different injection methods (ARG vs TMUX)
"""

import shutil
from pathlib import Path

from doeff_agents import (
    AgentType,
    LaunchConfig,
    capture_output,
    monitor_session,
    register_adapter,
    session_scope,
)
from doeff_agents.adapters.base import InjectionMethod

# =============================================================================
# Example 1: Simple Custom Adapter (ARG injection)
# =============================================================================


class AiderAdapter:
    """
    Adapter for Aider (https://aider.chat).

    Aider is an AI pair programming tool that works with many LLMs.
    It accepts prompts as command-line arguments.
    """

    @property
    def agent_type(self) -> AgentType:
        return AgentType.CUSTOM

    def is_available(self) -> bool:
        """Check if aider is installed."""
        return shutil.which("aider") is not None

    def launch_command(self, cfg: LaunchConfig) -> list[str]:
        """Build the command as an argv list."""
        args = ["aider", "--yes-always"]  # Auto-confirm prompts

        if cfg.profile:
            args.extend(["--model", cfg.profile])

        if cfg.prompt:
            args.extend(["--message", cfg.prompt])

        return args

    @property
    def injection_method(self) -> InjectionMethod:
        # Prompt is passed via command line
        return InjectionMethod.ARG

    @property
    def ready_pattern(self) -> str | None:
        # Not needed for ARG injection
        return None

    @property
    def status_bar_lines(self) -> int:
        # Lines to skip when hashing (Aider's status bar)
        return 3


# =============================================================================
# Example 2: Interactive Adapter (TMUX injection)
# =============================================================================


class ReplitAgentAdapter:
    """
    Adapter for Replit Agent (hypothetical example).

    Some agents launch interactively and need the prompt sent
    via tmux after they're ready. This adapter demonstrates
    the TMUX injection method.
    """

    @property
    def agent_type(self) -> AgentType:
        return AgentType.CUSTOM

    def is_available(self) -> bool:
        return shutil.which("replit-agent") is not None

    def launch_command(self, cfg: LaunchConfig) -> list[str]:
        """Launch command without the prompt (it's sent later via tmux)."""
        args = ["replit-agent"]

        if cfg.profile:
            args.extend(["--profile", cfg.profile])

        # Note: prompt is NOT included here - it's sent via tmux
        return args

    @property
    def injection_method(self) -> InjectionMethod:
        # Prompt is sent via tmux after agent is ready
        return InjectionMethod.TMUX

    @property
    def ready_pattern(self) -> str | None:
        # Regex pattern to detect when agent is ready for input
        return r"Ready for input|Enter your prompt|>"

    @property
    def status_bar_lines(self) -> int:
        return 2


# =============================================================================
# Example 3: Adapter with Custom Status Detection
# =============================================================================


class ContinueDevAdapter:
    """
    Adapter for Continue.dev CLI.

    This example shows an adapter with custom UI patterns for
    status detection. Different agents have different UI elements
    that indicate various states.
    """

    # Custom UI patterns for this specific agent (noqa for example simplicity)
    UI_PATTERNS: list[str] = [  # noqa: RUF012
        "Continue>",
        "Thinking...",
        "[continue]",
    ]

    BLOCKED_PATTERNS: list[str] = [  # noqa: RUF012
        "Press Enter to confirm",
        "Choose an option:",
        "Continue>",
    ]

    @property
    def agent_type(self) -> AgentType:
        return AgentType.CUSTOM

    def is_available(self) -> bool:
        return shutil.which("continue") is not None

    def launch_command(self, cfg: LaunchConfig) -> list[str]:
        args = ["continue", "chat"]

        if cfg.prompt:
            args.extend(["--message", cfg.prompt])

        return args

    @property
    def injection_method(self) -> InjectionMethod:
        return InjectionMethod.ARG

    @property
    def ready_pattern(self) -> str | None:
        return None

    @property
    def status_bar_lines(self) -> int:
        return 4


# =============================================================================
# Usage Examples
# =============================================================================


def use_custom_adapter() -> None:
    """Demonstrate using a custom adapter."""
    # Register the custom adapter
    register_adapter(AgentType.CUSTOM, AiderAdapter)  # type: ignore

    print("Registered AiderAdapter for AgentType.CUSTOM")

    # Check if the adapter is available
    from doeff_agents import get_adapter

    adapter = get_adapter(AgentType.CUSTOM)
    if not adapter.is_available():
        print("Aider is not installed. Install with: pip install aider-chat")
        return

    # Create a config using the custom adapter
    config = LaunchConfig(
        agent_type=AgentType.CUSTOM,  # Uses our registered adapter
        work_dir=Path.cwd(),
        prompt="List the Python files in this directory",
        profile="gpt-4",  # Model to use (passed to --model)
    )

    print(f"Launch command: {adapter.launch_command(config)}")
    print(f"Injection method: {adapter.injection_method.value}")

    # Run a session with the custom adapter
    import time

    session_name = f"aider-{int(time.time())}"

    with session_scope(session_name, config) as session:
        print(f"Session started: {session.session_name}")

        # Monitor for a short time
        for _ in range(30):
            new_status = monitor_session(session)
            if new_status:
                print(f"Status: {new_status.value}")

            if session.is_terminal:
                break

            time.sleep(1)

        print("\nOutput:")
        print(capture_output(session, lines=30))


def demo_adapter_protocol() -> None:
    """Show the adapter protocol without actually running."""
    print("=" * 60)
    print("Adapter Protocol Demonstration")
    print("=" * 60)

    adapters = [
        ("Aider", AiderAdapter()),
        ("ReplitAgent", ReplitAgentAdapter()),
        ("ContinueDev", ContinueDevAdapter()),
    ]

    for name, adapter in adapters:
        print(f"\n{name} Adapter:")
        print(f"  Agent Type: {adapter.agent_type.value}")
        print(f"  Available: {adapter.is_available()}")
        print(f"  Injection: {adapter.injection_method.value}")
        print(f"  Ready Pattern: {adapter.ready_pattern}")
        print(f"  Status Bar Lines: {adapter.status_bar_lines}")

        # Show example command
        config = LaunchConfig(
            agent_type=AgentType.CUSTOM,
            work_dir=Path("/tmp"),
            prompt="Hello, World!",
            profile="gpt-4",
        )
        cmd = adapter.launch_command(config)
        print(f"  Command: {' '.join(cmd)}")


if __name__ == "__main__":
    # Show adapter protocol without running
    demo_adapter_protocol()

    # Uncomment to actually use the adapter (requires aider installed)
    # use_custom_adapter()
