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

from doeff import do
from doeff.effects.writer import slog
from doeff_preset import preset_handlers

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
    Sleep,
    agent_effectful_handlers,
    configure_mock_session,
    mock_agent_handlers,
    with_session,
)


@do
def interactive_workflow(handle: SessionHandle):
    """Custom logic to run within a session.
    
    This function yields effects and is called within with_session bracket.
    """
    yield slog(step="interactive", msg="Starting interactive workflow")
    
    max_interactions = 3
    interaction_count = 0
    iteration = 0
    
    while iteration < 120:  # 2 minute timeout
        observation = yield Monitor(handle)
        
        if observation.output_changed:
            yield slog(
                step="status",
                iteration=iteration,
                status=observation.status.value,
            )
        
        if observation.is_terminal:
            yield slog(step="terminal", status=observation.status.value)
            break
        
        # Interact with the agent when blocked
        if observation.status == SessionStatus.BLOCKED and interaction_count < max_interactions:
            interaction_count += 1
            
            # Capture current output to see what the agent did
            output = yield Capture(handle, lines=20)
            yield slog(
                step="captured",
                interaction=interaction_count,
                preview=output[-300:] if output else "",
            )
            
            # Send follow-up instruction based on interaction count
            if interaction_count == 1:
                yield slog(step="sending", msg="Add docstring and type hints")
                yield Send(handle, "Add a docstring and type hints to the function.")
            elif interaction_count == 2:
                yield slog(step="sending", msg="Add test cases")
                yield Send(handle, "Add a few test cases to verify the function works.")
            elif interaction_count == 3:
                yield slog(step="sending", msg="Done, thank you")
                yield Send(handle, "That's perfect, thank you!")
        
        iteration += 1
        yield Sleep(1.0)
    
    # Final output
    final_output = yield Capture(handle, lines=50)
    yield slog(step="final", output_length=len(final_output))
    
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
    yield slog(step="start", session_name=session_name)
    
    # with_session is a bracket: Launch -> use -> Stop (always)
    result = yield from with_session(
        session_name,
        config,
        interactive_workflow,
    )
    
    yield slog(step="complete", interactions=result["interactions"])
    return result


@do
def demonstrate_exception_safety(session_name: str, config: LaunchConfig):
    """Show that cleanup happens even when exceptions occur.
    
    The with_session bracket ensures Stop is called in the finally block.
    """
    yield slog(step="exception_demo", msg="Starting exception safety demo")
    
    def raise_after_work(handle: SessionHandle):
        """Inner function that simulates work then raises."""
        yield slog(step="work", msg="Doing some work...")
        
        # Monitor once
        observation = yield Monitor(handle)
        yield slog(step="monitored", status=observation.status.value)
        
        # Simulate some work
        yield Sleep(1.0)
        
        # Simulate an error
        yield slog(step="error", msg="About to raise exception!")
        raise RuntimeError("Simulated error during processing!")
    
    try:
        yield from with_session(session_name, config, raise_after_work)
    except RuntimeError as e:
        yield slog(step="caught", error=str(e))
        yield slog(step="cleanup", msg="Session was still cleaned up automatically!")
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
        MockSessionScript([
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
        scoped_handlers=(preset_handlers(),),
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
        MockSessionScript([
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
        scoped_handlers=(preset_handlers(),),
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
        scoped_handlers=(preset_handlers(),),
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
