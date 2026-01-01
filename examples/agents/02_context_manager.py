#!/usr/bin/env python3
"""
Context Manager Example

This example demonstrates using `session_scope` for automatic cleanup.
The context manager ensures the session is stopped even if an exception occurs.

Benefits:
- Automatic cleanup on exit
- Exception-safe session management
- Cleaner code structure
"""

import sys
import time
from pathlib import Path

from doeff_agents import (
    AgentType,
    LaunchConfig,
    SessionStatus,
    capture_output,
    monitor_session,
    send_message,
    session_scope,
)


def run_with_context_manager() -> None:
    """Run an agent session using the context manager."""
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Create a simple Python function that calculates the factorial of a number.",
    )

    session_name = f"factorial-demo-{int(time.time())}"

    print(f"Starting session: {session_name}")
    print("Using context manager for automatic cleanup...")
    print("-" * 50)

    # The session_scope context manager handles cleanup automatically
    with session_scope(session_name, config) as session:
        print(f"Session running with pane: {session.pane_id}")

        # Monitor until completion or timeout
        timeout = 120  # 2 minutes
        start = time.time()
        interaction_count = 0

        while not session.is_terminal and (time.time() - start) < timeout:
            new_status = monitor_session(session)

            if new_status:
                print(f"[{time.time() - start:.1f}s] Status: {new_status.value}")

            # Interact with the agent when blocked
            if session.status == SessionStatus.BLOCKED and interaction_count < 3:
                interaction_count += 1

                # Capture current output to see what the agent did
                output = capture_output(session, lines=20)
                print(f"\n--- Agent output preview ---\n{output[-500:]}\n---")

                # Send follow-up instruction
                if interaction_count == 1:
                    print("Sending: Add docstring and type hints...")
                    send_message(session, "Add a docstring and type hints to the function.")
                elif interaction_count == 2:
                    print("Sending: Add test cases...")
                    send_message(session, "Add a few test cases to verify the function works.")
                elif interaction_count == 3:
                    print("Sending: Done, thank you...")
                    send_message(session, "That's perfect, thank you!")

            time.sleep(1)

        # Final output
        print("-" * 50)
        print("Final output:")
        print(capture_output(session, lines=50))
        print(f"\nSession ended with status: {session.status.value}")

    # Session is automatically stopped here, even if an exception occurred
    print("\nSession cleanup complete (handled by context manager)")


def demonstrate_exception_safety() -> None:
    """Show that cleanup happens even when exceptions occur."""
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Say hello",
    )

    session_name = f"exception-demo-{int(time.time())}"

    print("\n" + "=" * 50)
    print("Demonstrating exception safety...")
    print("=" * 50)

    try:
        with session_scope(session_name, config) as session:
            print(f"Session started: {session.session_name}")

            # Simulate some work
            time.sleep(2)

            # Simulate an error
            raise RuntimeError("Simulated error during processing!")

    except RuntimeError as e:
        print(f"Caught exception: {e}")
        print("Session was still cleaned up automatically!")


if __name__ == "__main__":
    # Check if Claude is available before running
    import shutil

    if not shutil.which("claude"):
        print("Warning: Claude CLI not found. Install with:")
        print("  npm install -g @anthropic/claude-code")
        print("\nThis example requires the Claude CLI to run.")
        sys.exit(1)

    run_with_context_manager()

    # Uncomment to see exception safety in action:
    # demonstrate_exception_safety()
