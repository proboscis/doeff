#!/usr/bin/env python3
"""
Custom Adapter Example - Using doeff Effects API

This example shows how to create a custom adapter for an agent that isn't
built into doeff-agents. The adapter pattern allows you to integrate any
command-line agent that runs in a terminal.

Key concepts:
- Implementing the AgentAdapter protocol
- Using adapters with the effects-based API
- Testing custom adapters with async_run and mock handlers
"""

import asyncio
import shutil
import time
from pathlib import Path

from _runtime import run_program
from doeff_agents import (
    AgentType,
    Capture,
    Launch,
    LaunchConfig,
    MockSessionScript,
    Monitor,
    SessionHandle,
    SessionStatus,
    Stop,
    agent_effectful_handlers,
    configure_mock_session,
    mock_agent_handlers,
)
from doeff_agents.adapters.base import AgentAdapter, InjectionMethod, LaunchParams
from doeff_time import Delay

from doeff import do, slog

# =============================================================================
# Example 1: Simple Custom Adapter (terminal prompt injection)
# =============================================================================


class AiderAdapter(AgentAdapter):
    """
    Adapter for Aider (https://aider.chat).

    Aider is an AI pair programming tool that works with many LLMs.
    This example keeps the agent in a live terminal session; prompts are
    sent after launch by the session backend.
    """

    @property
    def agent_type(self) -> AgentType:
        return AgentType.CUSTOM

    def is_available(self) -> bool:
        """Check if aider is installed."""
        return shutil.which("aider") is not None

    def launch_command(self, params: LaunchParams) -> list[str]:
        """Build the command as an argv list."""
        args = ["aider", "--yes-always"]  # Auto-confirm prompts

        if params.model:
            args.extend(["--model", params.model])

        return args

    @property
    def injection_method(self) -> InjectionMethod:
        return InjectionMethod.TMUX

    @property
    def ready_pattern(self) -> str | None:
        return None

    @property
    def status_bar_lines(self) -> int:
        # Lines to skip when hashing (Aider's status bar)
        return 3


# =============================================================================
# Example 2: Interactive Adapter (TMUX injection)
# =============================================================================


class ReplitAgentAdapter(AgentAdapter):
    """
    Adapter for Replit Agent (hypothetical example).

    Some agents launch interactively and need the prompt sent
    through the live terminal after they're ready. This adapter demonstrates
    the terminal injection method.
    """

    @property
    def agent_type(self) -> AgentType:
        return AgentType.CUSTOM

    def is_available(self) -> bool:
        return shutil.which("replit-agent") is not None

    def launch_command(self, params: LaunchParams) -> list[str]:
        """Launch command without the prompt (it's sent later via tmux)."""
        args = ["replit-agent"]

        if params.model:
            args.extend(["--profile", params.model])

        # Note: prompt is NOT included here - it is sent after launch.
        return args

    @property
    def injection_method(self) -> InjectionMethod:
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


class ContinueDevAdapter(AgentAdapter):
    """
    Adapter for Continue.dev CLI.

    This example shows an adapter with custom UI patterns for
    status detection. Different agents have different UI elements
    that indicate various states.
    """

    # Custom UI patterns for this specific agent
    UI_PATTERNS: list[str] = [  # noqa: RUF012 - framework class attribute is intentionally static metadata
        "Continue>",
        "Thinking...",
        "[continue]",
    ]

    BLOCKED_PATTERNS: list[str] = [  # noqa: RUF012 - framework class attribute is intentionally static metadata
        "Press Enter to confirm",
        "Choose an option:",
        "Continue>",
    ]

    @property
    def agent_type(self) -> AgentType:
        return AgentType.CUSTOM

    def is_available(self) -> bool:
        return shutil.which("continue") is not None

    def launch_command(self, params: LaunchParams) -> list[str]:
        args = ["continue", "chat"]

        return args

    @property
    def injection_method(self) -> InjectionMethod:
        return InjectionMethod.TMUX

    @property
    def ready_pattern(self) -> str | None:
        return None

    @property
    def status_bar_lines(self) -> int:
        return 4


# =============================================================================
# Effects-based Workflow
# =============================================================================


@do
def custom_adapter_workflow(session_name: str, config: LaunchConfig):
    """Run a session with any adapter using effects.

    The adapter is determined by the agent_type in the config.
    """
    yield slog("start", session_name=session_name, agent_type=config.agent_type.value)

    handle: SessionHandle = yield Launch(
        session_name,
        agent_type=config.agent_type,
        work_dir=config.work_dir,
        prompt=config.prompt,
    )
    yield slog("launched", session_id=handle.session_id)

    final_status = SessionStatus.PENDING

    try:
        for _iteration in range(30):
            observation = yield Monitor(handle)
            final_status = observation.status

            if observation.output_changed:
                yield slog("status", status=observation.status.value)

            if observation.is_terminal:
                break

            yield Delay(1.0)

        output = yield Capture(handle, lines=30)
        yield slog("complete", status=final_status.value)

        return {
            "session_name": session_name,
            "status": final_status.value,
            "output": output,
        }

    finally:
        yield Stop(handle)


# =============================================================================
# Demo Functions
# =============================================================================


def demo_adapter_protocol() -> None:
    """Show the adapter protocol without actually running."""
    print("=" * 60)
    print("Adapter Protocol Demonstration")
    print("=" * 60)

    adapters: list[tuple[str, AgentAdapter]] = [
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
        params = LaunchParams(
            work_dir=Path("/tmp"),
            prompt="Hello, World!",
            model="gpt-4",
        )
        cmd = adapter.launch_command(params)
        print(f"  Command: {' '.join(cmd)}")


async def run_with_mock_handlers() -> None:
    """Run the workflow with mock handlers."""
    print("\n" + "=" * 60)
    print("Running with mock handlers")
    print("=" * 60)

    session_name = f"aider-demo-{int(time.time())}"

    configure_mock_session(
        session_name,
        MockSessionScript(observations=[
            (SessionStatus.RUNNING, "Analyzing code..."),
            (SessionStatus.RUNNING, "Making changes..."),
            (SessionStatus.DONE, "Changes complete!\n\nModified: main.py"),
        ]),
    )

    config = LaunchConfig(
        agent_type=AgentType.CUSTOM,
        work_dir=Path.cwd(),
        prompt="List the Python files in this directory",
        model="gpt-4",
    )

    result = await run_program(
        custom_adapter_workflow(session_name, config),
        custom_handlers=mock_agent_handlers(),
    )
    print(f"\nResult: {result}")


async def run_with_real_tmux() -> None:
    """Run with real tmux (requires aider installed)."""
    if not shutil.which("aider"):
        print("Aider not installed, skipping real example")
        return

    print("\n" + "=" * 60)
    print("Running with real tmux + Aider")
    print("=" * 60)

    config = LaunchConfig(
        agent_type=AgentType.CUSTOM,
        work_dir=Path.cwd(),
        prompt="List the Python files in this directory",
        model="gpt-4",
    )

    session_name = f"aider-real-{int(time.time())}"

    result = await run_program(
        custom_adapter_workflow(session_name, config),
        custom_handlers=agent_effectful_handlers(),
    )
    print(f"\nResult: {result}")


async def main() -> None:
    """Run all examples."""
    # Show adapter protocol (always works)
    demo_adapter_protocol()

    # Run with mock handlers
    await run_with_mock_handlers()

    # Uncomment to run with real tmux + aider
    # await run_with_real_tmux()


if __name__ == "__main__":
    asyncio.run(main())
