#!/usr/bin/env python3
"""
Effects API Example - Complete doeff Integration

This example demonstrates the full effects-based API for agent session
management with doeff AsyncRuntime integration. Shows:

- @do decorated functions yielding fine-grained effects
- slog for structured logging
- preset_handlers for log display
- agent_effectful_handlers for real tmux
- mock_agent_handlers for testing
- AsyncRuntime for all execution

Key benefits:
- Effects are pure data, handlers perform side effects
- MockAgentHandler enables deterministic testing
- Programs compose effects for reusable workflows
- Full doeff ecosystem integration
"""

import asyncio
import time
from pathlib import Path

from doeff import AsyncRuntime, do
from doeff.effects.writer import slog
from doeff_preset import preset_handlers

from doeff_agents import (
    # Types
    AgentType,
    # Effects
    Capture,
    CeskMockSessionScript,
    Launch,
    LaunchConfig,
    Monitor,
    Observation,
    Send,
    SessionHandle,
    SessionStatus,
    Sleep,
    Stop,
    # CESK Handlers
    agent_effectful_handlers,
    configure_mock_session,
    mock_agent_handlers,
    # Programs
    run_agent_to_completion,
    with_session,
)


# =============================================================================
# Example 1: Direct Effect Usage with @do
# =============================================================================


@do
def direct_effects_workflow(session_name: str, config: LaunchConfig):
    """Demonstrate using effects directly with @do decorator.
    
    Each yield is an effect that gets interpreted by the runtime's handlers.
    """
    yield slog(step="example1", msg="Direct Effect Usage")
    
    # Launch effect
    handle: SessionHandle = yield Launch(session_name, config)
    yield slog(step="launched", session_name=session_name, pane_id=handle.pane_id)
    
    final_status = SessionStatus.PENDING
    
    # Monitor until terminal
    try:
        for iteration in range(10):
            observation: Observation = yield Monitor(handle)
            final_status = observation.status
            yield slog(step="monitor", iteration=iteration, status=observation.status.value)
            
            if observation.is_terminal:
                break
            
            # Sleep between polls
            yield Sleep(1.0)
        
        # Capture final output
        output: str = yield Capture(handle, lines=50)
        yield slog(step="captured", output_length=len(output))
        
        return {
            "status": final_status.value,
            "output": output,
        }
    
    finally:
        # Stop session
        yield Stop(handle)
        yield slog(step="stopped", session_name=session_name)


# =============================================================================
# Example 2: Using High-Level Programs
# =============================================================================


@do
def high_level_programs_workflow(session_name: str, config: LaunchConfig):
    """Demonstrate using high-level Programs with @do.
    
    Programs like run_agent_to_completion compose fine-grained effects
    into reusable workflows.
    """
    yield slog(step="example2", msg="High-Level Programs")
    
    # run_agent_to_completion is a generator that yields effects
    # We use yield from to delegate to it
    result = yield from run_agent_to_completion(
        session_name,
        config,
        poll_interval=0.5,
    )
    
    yield slog(
        step="complete",
        succeeded=result.succeeded,
        status=result.final_status.value,
        iterations=result.iterations,
    )
    
    return {
        "succeeded": result.succeeded,
        "status": result.final_status.value,
        "output": result.output,
        "pr_url": result.pr_url,
    }


# =============================================================================
# Example 3: Bracket Pattern with with_session
# =============================================================================


@do
def bracket_pattern_workflow(session_name: str, config: LaunchConfig):
    """Demonstrate the with_session bracket pattern.
    
    with_session ensures Stop is called even on exceptions.
    """
    yield slog(step="example3", msg="Bracket Pattern (with_session)")
    
    def use_session(handle: SessionHandle):
        """Custom logic to run within the session."""
        # Monitor once
        obs = yield Monitor(handle)
        yield slog(step="initial_status", status=obs.status.value)
        
        # Wait for BLOCKED status
        while obs.status != SessionStatus.BLOCKED:
            yield Sleep(0.5)
            obs = yield Monitor(handle)
        
        # Send a follow-up message
        yield Send(handle, "Thanks, that's helpful!")
        yield slog(step="sent_followup")
        
        # Capture output
        output = yield Capture(handle, lines=20)
        return output
    
    # with_session ensures Stop is called even on error
    output = yield from with_session(session_name, config, use_session)
    
    yield slog(step="bracket_complete", output_length=len(output) if output else 0)
    
    return {"output": output}


# =============================================================================
# Example 4: Testing with Mock Handlers
# =============================================================================


@do
def testable_workflow(session_name: str, config: LaunchConfig):
    """A workflow designed for easy testing.
    
    mock_agent_handlers allows deterministic testing without real tmux.
    """
    yield slog(step="example4", msg="Testing with mock handlers")
    
    handle = yield Launch(session_name, config)
    
    observations = []
    final_status = SessionStatus.PENDING
    
    try:
        for _ in range(10):
            obs = yield Monitor(handle)
            observations.append(obs.status)
            final_status = obs.status
            
            yield slog(step="observed", status=obs.status.value)
            
            if obs.is_terminal:
                break
            
            yield Sleep(0.5)
        
        output = yield Capture(handle, lines=50)
        
        return {
            "observations": [s.value for s in observations],
            "final_status": final_status.value,
            "output": output,
        }
    
    finally:
        yield Stop(handle)


# =============================================================================
# Running Examples with AsyncRuntime
# =============================================================================


async def run_direct_effects_example() -> None:
    """Run Example 1: Direct Effect Usage."""
    print("=" * 60)
    print("Example 1: Direct Effect Usage")
    print("=" * 60)
    
    session_name = f"demo-{int(time.time())}"
    
    initial_store = {}
    configure_mock_session(
        initial_store,
        session_name,
        CeskMockSessionScript([
            (SessionStatus.BOOTING, "Agent starting..."),
            (SessionStatus.RUNNING, "Processing request..."),
            (SessionStatus.DONE, "Task completed!"),
        ]),
    )
    
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Write a hello world function",
    )
    
    handlers = {
        **preset_handlers(),
        **mock_agent_handlers(),
    }
    runtime = AsyncRuntime(handlers=handlers, initial_store=initial_store)
    
    result = await runtime.run(direct_effects_workflow(session_name, config))
    print(f"\nResult: {result}")


async def run_high_level_programs_example() -> None:
    """Run Example 2: High-Level Programs."""
    print("\n" + "=" * 60)
    print("Example 2: High-Level Programs")
    print("=" * 60)
    
    session_name = f"program-demo-{int(time.time())}"
    
    initial_store = {}
    configure_mock_session(
        initial_store,
        session_name,
        CeskMockSessionScript([
            (SessionStatus.RUNNING, "Working..."),
            (SessionStatus.DONE, "Complete!"),
        ]),
    )
    
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="List files in current directory",
    )
    
    handlers = {
        **preset_handlers(),
        **mock_agent_handlers(),
    }
    runtime = AsyncRuntime(handlers=handlers, initial_store=initial_store)
    
    result = await runtime.run(high_level_programs_workflow(session_name, config))
    print(f"\nResult: {result}")


async def run_bracket_pattern_example() -> None:
    """Run Example 3: Bracket Pattern."""
    print("\n" + "=" * 60)
    print("Example 3: Bracket Pattern (with_session)")
    print("=" * 60)
    
    session_name = f"bracket-demo-{int(time.time())}"
    
    initial_store = {}
    configure_mock_session(
        initial_store,
        session_name,
        CeskMockSessionScript([
            (SessionStatus.RUNNING, "Processing..."),
            (SessionStatus.BLOCKED, "Need input..."),
        ]),
    )
    
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Help me write code",
    )
    
    handlers = {
        **preset_handlers(),
        **mock_agent_handlers(),
    }
    runtime = AsyncRuntime(handlers=handlers, initial_store=initial_store)
    
    result = await runtime.run(bracket_pattern_workflow(session_name, config))
    print(f"\nResult: {result}")


async def run_testing_example() -> None:
    """Run Example 4: Testing with Mock Handlers."""
    print("\n" + "=" * 60)
    print("Example 4: Testing with Mock Handlers")
    print("=" * 60)
    
    session_name = f"test-{int(time.time())}"
    
    initial_store = {}
    configure_mock_session(
        initial_store,
        session_name,
        CeskMockSessionScript([
            (SessionStatus.RUNNING, "Analyzing code..."),
            (SessionStatus.RUNNING, "Found 3 issues..."),
            (SessionStatus.BLOCKED, "Review complete. Approve?"),
            (SessionStatus.DONE, "Changes applied!"),
        ]),
    )
    
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Review this code",
    )
    
    handlers = {
        **preset_handlers(),
        **mock_agent_handlers(),
    }
    runtime = AsyncRuntime(handlers=handlers, initial_store=initial_store)
    
    result = await runtime.run(testable_workflow(session_name, config))
    
    # Verify behavior
    print("\nTest assertions:")
    print(f"  Final status is DONE: {result['final_status'] == 'done'}")
    expected_statuses = ['running', 'running', 'blocked', 'done']
    print(f"  Observed expected statuses: {result['observations'] == expected_statuses}")
    print(f"\nResult: {result}")


async def run_with_real_tmux() -> None:
    """Run with real tmux (requires tmux + claude CLI)."""
    import shutil
    
    if not shutil.which("tmux"):
        print("tmux not available, skipping")
        return
    
    if not shutil.which("claude"):
        print("Claude CLI not available, skipping")
        return
    
    print("\n" + "=" * 60)
    print("Example 5: Real Tmux Handler")
    print("=" * 60)
    
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Write a hello world function",
    )
    
    session_name = f"real-tmux-{int(time.time())}"
    
    # Combine preset + real agent handlers
    handlers = {
        **preset_handlers(),
        **agent_effectful_handlers(),
    }
    
    runtime = AsyncRuntime(handlers=handlers)
    result = await runtime.run(direct_effects_workflow(session_name, config))
    
    print(f"\nResult: {result}")


async def main() -> None:
    """Run all examples."""
    await run_direct_effects_example()
    await run_high_level_programs_example()
    await run_bracket_pattern_example()
    await run_testing_example()
    
    # Real tmux (uncomment to test)
    # await run_with_real_tmux()


if __name__ == "__main__":
    asyncio.run(main())
