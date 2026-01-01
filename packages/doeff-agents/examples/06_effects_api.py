#!/usr/bin/env python3
"""
Effects API Example

This example demonstrates using the effects-based API for agent session
management. The effects API provides:

- Immutable SessionHandle instead of mutable AgentSession
- Fine-grained effects (Launch, Monitor, Capture, Send, Stop, Sleep)
- Composable Programs for complex workflows
- Testable handlers (mock handler for testing without tmux)

Key benefits:
- Effects are pure data, handlers perform side effects
- MockAgentHandler enables deterministic testing
- Programs compose effects for reusable workflows
"""

from pathlib import Path

from doeff_agents import (
    # Types
    AgentType,
    # Effects
    Capture,
    Launch,
    LaunchConfig,
    # Handlers
    MockAgentHandler,
    MockSessionScript,
    Monitor,
    Observation,
    Send,
    SessionHandle,
    SessionStatus,
    Sleep,
    Stop,
    dispatch_effect,
    # Programs
    run_agent_to_completion,
    with_session,
)

# =============================================================================
# Example 1: Direct Effect Usage with Handler
# =============================================================================


def run_with_effects_directly() -> None:
    """Demonstrate using effects directly with a handler."""
    print("=" * 60)
    print("Example 1: Direct Effect Usage")
    print("=" * 60)

    # Create handler (use MockAgentHandler for demo without real tmux)
    handler = MockAgentHandler()

    # Configure mock behavior
    handler.configure_session(
        "demo-session",
        MockSessionScript([
            (SessionStatus.BOOTING, "Agent starting..."),
            (SessionStatus.RUNNING, "Processing request..."),
            (SessionStatus.BLOCKED, "Waiting for input..."),
            (SessionStatus.DONE, "Task completed!"),
        ]),
    )

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Write a hello world function",
    )

    # Launch effect
    launch_effect = Launch("demo-session", config)
    handle: SessionHandle = dispatch_effect(handler, launch_effect)
    print(f"Launched session: {handle}")

    # Monitor until terminal
    iteration = 0
    while True:
        monitor_effect = Monitor(handle)
        obs: Observation = dispatch_effect(handler, monitor_effect)
        print(f"  [{iteration}] Status: {obs.status.value}")

        if obs.is_terminal:
            break

        # Sleep between polls (mock handler skips actual delay)
        sleep_effect = Sleep(1.0)
        dispatch_effect(handler, sleep_effect)
        iteration += 1

    # Capture final output
    capture_effect = Capture(handle, lines=50)
    output: str = dispatch_effect(handler, capture_effect)
    print(f"  Final output: {output}")

    # Stop session
    stop_effect = Stop(handle)
    dispatch_effect(handler, stop_effect)
    print("  Session stopped")


# =============================================================================
# Example 2: Using Program Generators
# =============================================================================


def run_program_with_handler(program, handler):
    """Execute a program generator with a handler.

    This simulates what a doeff interpreter would do.
    """
    try:
        effect = next(program)
    except StopIteration as stop:
        return stop.value

    while True:
        try:
            result = dispatch_effect(handler, effect)
            effect = program.send(result)
        except StopIteration as stop:
            return stop.value


def run_with_programs() -> None:
    """Demonstrate using high-level Programs."""
    print("\n" + "=" * 60)
    print("Example 2: High-Level Programs")
    print("=" * 60)

    handler = MockAgentHandler()
    handler.configure_session(
        "program-demo",
        MockSessionScript([
            (SessionStatus.RUNNING, "Working..."),
            (SessionStatus.DONE, "Complete!"),
        ]),
    )

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="List files in current directory",
    )

    # Create program (lazy - no execution yet)
    program = run_agent_to_completion(
        "program-demo",
        config,
        poll_interval=0.5,
    )

    # Execute program with handler
    result = run_program_with_handler(program, handler)

    print(f"  Succeeded: {result.succeeded}")
    print(f"  Final status: {result.final_status.value}")
    print(f"  Iterations: {result.iterations}")
    print(f"  Output: {result.output}")


# =============================================================================
# Example 3: Custom Program with with_session Bracket
# =============================================================================


def run_with_bracket() -> None:
    """Demonstrate the with_session bracket pattern."""
    print("\n" + "=" * 60)
    print("Example 3: Bracket Pattern (with_session)")
    print("=" * 60)

    handler = MockAgentHandler()
    handler.configure_session(
        "bracket-demo",
        MockSessionScript([
            (SessionStatus.RUNNING, "Processing..."),
            (SessionStatus.BLOCKED, "Need input..."),
        ]),
        initial_output="Interactive session ready",
    )

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Help me write code",
    )

    def use_session(handle: SessionHandle):
        """Custom logic to run within the session."""
        # Monitor once
        obs = yield Monitor(handle)
        print(f"  Initial status: {obs.status.value}")

        # Wait for BLOCKED status
        while obs.status != SessionStatus.BLOCKED:
            yield Sleep(0.5)
            obs = yield Monitor(handle)

        # Send a follow-up message
        yield Send(handle, "Thanks, that's helpful!")
        print("  Sent follow-up message")

        # Capture output
        output = yield Capture(handle, lines=20)
        return output

    # with_session ensures Stop is called even on error
    program = with_session("bracket-demo", config, use_session)
    result = run_program_with_handler(program, handler)

    print(f"  Result: {result}")
    print(f"  Session was stopped: {handler._statuses.get('bracket-demo') == SessionStatus.STOPPED}")


# =============================================================================
# Example 4: Testing with MockAgentHandler
# =============================================================================


def demonstrate_testing() -> None:
    """Show how MockAgentHandler enables testing."""
    print("\n" + "=" * 60)
    print("Example 4: Testing with MockAgentHandler")
    print("=" * 60)

    # Setup mock with specific script
    handler = MockAgentHandler()
    handler.configure_session(
        "test-session",
        MockSessionScript([
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

    # Run program
    program = run_agent_to_completion("test-session", config)
    result = run_program_with_handler(program, handler)

    # Verify behavior
    print("  Test assertions:")
    print(f"    ✓ Final status is DONE: {result.final_status == SessionStatus.DONE}")
    print(f"    ✓ Ran expected iterations: {result.iterations == 3}")
    # Mock handler records sleep calls but doesn't actually wait
    print(f"    ✓ Sleep calls recorded (but instant): {len(handler._sleep_calls)} calls")


# =============================================================================
# Example 5: Real Tmux Usage (when available)
# =============================================================================


def run_with_real_tmux() -> None:
    """Demonstrate real tmux usage (requires tmux and Claude CLI)."""
    print("\n" + "=" * 60)
    print("Example 5: Real Tmux Handler")
    print("=" * 60)

    import shutil

    if not shutil.which("tmux"):
        print("  ⚠ tmux not available, skipping real example")
        return

    if not shutil.which("claude"):
        print("  ⚠ Claude CLI not available, skipping real example")
        return

    print("  To use real tmux:")
    print("    handler = TmuxAgentHandler()")
    print("    handle = dispatch_effect(handler, Launch(...))")
    print("    # Effects now interact with real tmux sessions")
    print()
    print("  The TmuxAgentHandler:")
    print("    - Creates real tmux sessions")
    print("    - Monitors actual agent output")
    print("    - Captures real pane content")
    print("    - Sleeps for actual duration")


if __name__ == "__main__":
    run_with_effects_directly()
    run_with_programs()
    run_with_bracket()
    demonstrate_testing()
    run_with_real_tmux()
