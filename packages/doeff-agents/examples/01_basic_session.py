#!/usr/bin/env python3
"""
Basic Session Example - Using doeff Effects API

This example demonstrates the fundamental operations of doeff-agents
using the effects-based approach with doeff's async_run entrypoint:
- Launching an agent session with Launch effect
- Monitoring session status with Monitor effect
- Capturing output with Capture effect
- Stopping a session with Stop effect

The effects API provides:
- Composable, testable programs
- Integration with doeff preset handlers for logging
- Mock handlers for testing without real tmux
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
    Launch,
    LaunchConfig,
    Monitor,
    MockSessionScript,
    SessionHandle,
    SessionStatus,
    Sleep,
    Stop,
    agent_effectful_handlers,
    configure_mock_session,
    mock_agent_handlers,
)


@do
def basic_session_workflow(session_name: str, config: LaunchConfig):
    """Run a basic agent session using effects.
    
    This program yields fine-grained effects that are interpreted
    by the runtime's handlers.
    """
    yield slog(step="launch", session_name=session_name, work_dir=str(config.work_dir))
    yield slog(step="launch", agent_type=config.agent_type.value)
    
    # Launch the session
    handle: SessionHandle = yield Launch(session_name, config)
    yield slog(step="launched", pane_id=handle.pane_id)
    
    try:
        # Monitor the session until terminal state or timeout
        max_iterations = 60
        iteration = 0
        final_status = SessionStatus.PENDING
        
        while iteration < max_iterations:
            observation = yield Monitor(handle)
            final_status = observation.status
            
            if observation.output_changed:
                yield slog(
                    step="status",
                    iteration=iteration,
                    status=observation.status.value,
                )
            
            if observation.is_terminal:
                yield slog(step="terminal", status=observation.status.value)
                break
            
            # If blocked (waiting for input), log it
            if observation.status == SessionStatus.BLOCKED:
                yield slog(step="blocked", msg="Agent is waiting for input")
            
            iteration += 1
            yield Sleep(1.0)
        
        # Capture final output
        output: str = yield Capture(handle, lines=30)
        yield slog(step="output", lines=30, preview=output[-200:] if output else "")
        
        return {
            "session_name": session_name,
            "final_status": final_status.value,
            "output": output,
            "iterations": iteration,
        }
    
    finally:
        # Always clean up the session
        yield slog(step="stopping", session_name=session_name)
        yield Stop(handle)
        yield slog(step="stopped")


async def run_with_mock_handlers() -> None:
    """Run the example with mock handlers (no real tmux needed)."""
    print("=" * 60)
    print("Running with mock handlers")
    print("=" * 60)
    
    session_name = f"demo-{int(time.time())}"

    # Configure mock session behavior
    configure_mock_session(
        session_name,
        MockSessionScript([
            (SessionStatus.BOOTING, "Agent starting..."),
            (SessionStatus.RUNNING, "Processing request..."),
            (SessionStatus.RUNNING, "Listing files..."),
            (SessionStatus.DONE, "Task completed!\n\nFiles:\n- README.md\n- main.py"),
        ]),
    )
    
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="List the files in the current directory and describe what you see.",
    )

    result = await run_program(
        basic_session_workflow(session_name, config),
        scoped_handlers=(preset_handlers(),),
        custom_handlers=mock_agent_handlers(),
    )
    print(f"\nResult: {result}")


async def run_with_real_tmux() -> None:
    """Run the example with real tmux (requires tmux + claude CLI)."""
    import shutil
    
    if not shutil.which("tmux"):
        print("tmux not available, skipping real tmux example")
        return
    
    if not shutil.which("claude"):
        print("Claude CLI not available, skipping real tmux example")
        return
    
    print("\n" + "=" * 60)
    print("Running with real tmux")
    print("=" * 60)
    
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="List the files in the current directory and describe what you see.",
    )
    
    session_name = f"demo-{int(time.time())}"
    
    result = await run_program(
        basic_session_workflow(session_name, config),
        scoped_handlers=(preset_handlers(),),
        custom_handlers=agent_effectful_handlers(),
    )
    print(f"\nResult: {result}")


async def main() -> None:
    """Run the examples."""
    await run_with_mock_handlers()
    
    # Uncomment to run with real tmux (requires tmux + claude CLI)
    # await run_with_real_tmux()


if __name__ == "__main__":
    asyncio.run(main())
