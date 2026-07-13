#!/usr/bin/env python3
"""
Context Manager Example - Using doeff Effects API

This example demonstrates using `with_session` for automatic cleanup.
The effects-based bracket pattern ensures the session is stopped
even if an exception occurs during execution.

Benefits:
- Automatic cleanup on exit via Stop effect in finally block
- Exception-safe session management
- Composable with other effects (slog, etc.)
- Testable with mock handlers
"""

import asyncio
import time
from pathlib import Path

from _runtime import run_program
from doeff_agents import (
    AgentType,
    Capture,
    LaunchConfig,
    MockSessionScript,
    Monitor,
    Send,
    SessionHandle,
    SessionStatus,
    agent_effectful_handlers,
    configure_mock_session,
    mock_agent_handlers,
    with_session,
)
from doeff_time import Delay

from doeff import do, program, slog


def interactive_workflow(handle: SessionHandle):
    """Custom logic to run within a session.

    This is a plain generator function (not @do-decorated): with_session
    drives it directly via the generator protocol (next/send/throw), so it
    must return a real generator rather than a Program/Expand tree.
    """
    yield slog(msg="Starting interactive workflow", step="interactive")

    max_interactions = 3
    interaction_count = 0
    iteration = 0

    while iteration < 120:  # 2 minute timeout
        observation = yield Monitor(handle)

        if observation.output_changed:
            yield slog(
                "status",
                iteration=iteration,
                status=observation.status.value,
            )

        if observation.is_terminal:
            yield slog("terminal", status=observation.status.value)
            break

        # Interact with the agent when blocked
        if observation.status == SessionStatus.BLOCKED and interaction_count < max_interactions:
            interaction_count += 1

            # Capture current output to see what the agent did
            output = yield Capture(handle, lines=20)
            yield slog(
                "captured",
                interaction=interaction_count,
                preview=output[-300:] if output else "",
            )

            # Send follow-up instruction based on interaction count
            if interaction_count == 1:
                yield slog(msg="Add docstring and type hints", step="sending")
                yield Send(handle, "Add a docstring and type hints to the function.")
            elif interaction_count == 2:
                yield slog(msg="Add test cases", step="sending")
                yield Send(handle, "Add a few test cases to verify the function works.")
            elif interaction_count == 3:
                yield slog(msg="Done, thank you", step="sending")
                yield Send(handle, "That's perfect, thank you!")

        iteration += 1
        yield Delay(1.0)

    # Final output
    final_output = yield Capture(handle, lines=50)
    yield slog("final", output_length=len(final_output))

    return {
        "interactions": interaction_count,
        "iterations": iteration,
        "output": final_output,
    }


@do
def run_with_bracket(session_name: str, config: LaunchConfig):
    """Run a session using the with_session bracket pattern.

    with_session ensures Stop is called even if an exception occurs.
    """
    yield slog("start", session_name=session_name)

    # with_session is a bracket: Launch -> use -> Stop (always). program()
    # lifts the plain-generator call into a Program call-tree node.
    result = yield program(lambda: with_session(session_name, config, interactive_workflow))

    yield slog("complete", interactions=result["interactions"])
    return result


@do
def demonstrate_exception_safety(session_name: str, config: LaunchConfig):
    """Show that cleanup happens even when exceptions occur.

    The with_session bracket ensures Stop is called in the finally block.
    """
    yield slog(msg="Starting exception safety demo", step="exception_demo")

    def raise_after_work(handle: SessionHandle):
        """Inner function that simulates work then raises."""
        yield slog(msg="Doing some work...", step="work")

        # Monitor once
        observation = yield Monitor(handle)
        yield slog("monitored", status=observation.status.value)

        # Simulate some work
        yield Delay(1.0)

        # Simulate an error
        yield slog(msg="About to raise exception!", step="error")
        raise RuntimeError("Simulated error during processing!")

    try:
        _ = yield program(lambda: with_session(session_name, config, raise_after_work))
    except RuntimeError as e:
        yield slog("caught", error=str(e))
        yield slog(msg="Session was still cleaned up automatically!", step="cleanup")
        return {"error": str(e), "cleanup_successful": True}

    return {"error": None, "cleanup_successful": True}


async def run_interactive_example() -> None:
    """Run the interactive workflow with mock handlers."""
    print("=" * 60)
    print("Interactive Workflow with Bracket Pattern")
    print("=" * 60)

    session_name = f"factorial-demo-{int(time.time())}"

    # Configure mock behavior
    configure_mock_session(
        session_name,
        MockSessionScript(observations=[
            (SessionStatus.RUNNING, "Creating factorial function..."),
            (SessionStatus.BLOCKED, "Function created. What next?"),
            (SessionStatus.RUNNING, "Adding docstring and type hints..."),
            (SessionStatus.BLOCKED, "Added documentation. Anything else?"),
            (SessionStatus.RUNNING, "Writing test cases..."),
            (SessionStatus.BLOCKED, "Tests added. All done?"),
            (SessionStatus.DONE, "Task completed!\n\ndef factorial(n: int) -> int:\n    ..."),
        ]),
    )

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Create a simple Python function that calculates the factorial of a number.",
    )

    result = await run_program(
        run_with_bracket(session_name, config),
        custom_handlers=mock_agent_handlers(),
    )
    print(f"\nResult: {result}")


async def run_exception_demo() -> None:
    """Demonstrate exception safety with mock handler."""
    print("\n" + "=" * 60)
    print("Exception Safety Demo")
    print("=" * 60)

    session_name = f"exception-demo-{int(time.time())}"

    configure_mock_session(
        session_name,
        MockSessionScript(observations=[
            (SessionStatus.RUNNING, "Working..."),
            (SessionStatus.RUNNING, "Still working..."),
        ]),
    )

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Say hello",
    )

    result = await run_program(
        demonstrate_exception_safety(session_name, config),
        custom_handlers=mock_agent_handlers(),
    )
    print(f"\nResult: {result}")


async def run_with_real_tmux() -> None:
    """Run with real tmux (requires tmux + claude CLI)."""
    import shutil

    if not shutil.which("tmux") or not shutil.which("claude"):
        print("tmux or Claude CLI not available, skipping real tmux example")
        return

    print("\n" + "=" * 60)
    print("Running with real tmux")
    print("=" * 60)

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Create a simple Python function that calculates the factorial of a number.",
    )

    session_name = f"factorial-{int(time.time())}"

    result = await run_program(
        run_with_bracket(session_name, config),
        custom_handlers=agent_effectful_handlers(),
    )
    print(f"\nResult: {result}")


async def main() -> None:
    """Run all examples."""
    await run_interactive_example()
    await run_exception_demo()

    # Uncomment to run with real tmux
    # await run_with_real_tmux()


if __name__ == "__main__":
    asyncio.run(main())
