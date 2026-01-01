#!/usr/bin/env python3
"""
Basic Session Example

This example demonstrates the fundamental operations of doeff-agents:
- Launching an agent session
- Monitoring session status
- Capturing output
- Stopping a session

Note: This example requires tmux and the Claude CLI to be installed.
"""

import time
from pathlib import Path

from doeff_agents import (
    AgentType,
    LaunchConfig,
    SessionStatus,
    capture_output,
    launch_session,
    monitor_session,
    stop_session,
)


def main() -> None:
    # Configure the agent session
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="List the files in the current directory and describe what you see.",
    )

    # Generate a unique session name
    session_name = f"demo-{int(time.time())}"

    print(f"Launching session: {session_name}")
    print(f"Working directory: {config.work_dir}")
    print(f"Agent type: {config.agent_type.value}")
    print("-" * 50)

    # Launch the session
    try:
        session = launch_session(session_name, config)
        print(f"Session started with pane ID: {session.pane_id}")
        print(f"Initial status: {session.status.value}")

        # Monitor the session for up to 60 seconds
        max_wait = 60
        start_time = time.time()

        while not session.is_terminal and (time.time() - start_time) < max_wait:
            # Check for status changes
            new_status = monitor_session(session)
            if new_status:
                print(f"Status changed: {new_status.value}")

            # If blocked (waiting for input), we could send a message
            if session.status == SessionStatus.BLOCKED:
                print("Agent is waiting for input...")
                # In a real application, you might send a follow-up message:
                # send_message(session, "Continue")

            time.sleep(1)

        # Capture and display the final output
        print("-" * 50)
        print("Final output (last 30 lines):")
        output = capture_output(session, lines=30)
        print(output)

        print("-" * 50)
        print(f"Final status: {session.status.value}")

    except Exception as e:
        print(f"Error: {e}")

    finally:
        # Always clean up the session
        print("Stopping session...")
        stop_session(session)
        print("Session stopped.")


if __name__ == "__main__":
    main()
