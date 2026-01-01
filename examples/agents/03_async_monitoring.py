#!/usr/bin/env python3
"""
Async Monitoring Example

This example demonstrates the async API for monitoring agent sessions.
The async API is useful when:
- Running multiple sessions concurrently
- Integrating with async web frameworks
- Non-blocking monitoring in event loops

Features shown:
- async_session_scope context manager
- async_monitor_session for non-blocking monitoring
- Running multiple sessions in parallel
"""

import asyncio
import time
from pathlib import Path

from doeff_agents import (
    AgentType,
    LaunchConfig,
    SessionStatus,
    async_monitor_session,
    async_session_scope,
    capture_output,
)


async def run_single_async_session() -> None:
    """Run a single session using async API."""
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Write a haiku about programming.",
    )

    session_name = f"async-demo-{int(time.time())}"

    print(f"Starting async session: {session_name}")

    def on_status_change(old: SessionStatus, new: SessionStatus, output: str | None) -> None:
        print(f"  Status: {old.value} -> {new.value}")

    def on_pr_detected(url: str) -> None:
        print(f"  PR Created: {url}")

    async with async_session_scope(session_name, config) as session:
        print(f"Session running: {session.pane_id}")

        # async_monitor_session runs until terminal state
        final_status = await async_monitor_session(
            session,
            poll_interval=0.5,
            on_status_change=on_status_change,
            on_pr_detected=on_pr_detected,
        )

        print(f"\nFinal status: {final_status.value}")
        print("Output:")
        print(capture_output(session, lines=20))


async def run_parallel_sessions() -> None:
    """Run multiple sessions in parallel."""
    tasks = [
        ("Write a function to reverse a string", "reverse-string"),
        ("Write a function to check if a number is prime", "is-prime"),
        ("Write a function to calculate fibonacci numbers", "fibonacci"),
    ]

    print("=" * 60)
    print("Running 3 agent sessions in parallel")
    print("=" * 60)

    async def run_task(prompt: str, name_suffix: str) -> tuple[str, SessionStatus, str]:
        """Run a single task and return results."""
        config = LaunchConfig(
            agent_type=AgentType.CLAUDE,
            work_dir=Path.cwd(),
            prompt=prompt,
        )

        session_name = f"parallel-{name_suffix}-{int(time.time())}"

        print(f"[{name_suffix}] Starting session...")

        async with async_session_scope(session_name, config) as session:

            def on_change(old: SessionStatus, new: SessionStatus, _: str | None) -> None:
                print(f"[{name_suffix}] {old.value} -> {new.value}")

            final_status = await async_monitor_session(
                session,
                poll_interval=1.0,
                on_status_change=on_change,
            )

            output = capture_output(session, lines=30)
            return name_suffix, final_status, output

    # Run all tasks concurrently
    start_time = time.time()
    results = await asyncio.gather(*[run_task(prompt, name) for prompt, name in tasks])
    elapsed = time.time() - start_time

    # Display results
    print("\n" + "=" * 60)
    print(f"All sessions completed in {elapsed:.1f}s")
    print("=" * 60)

    for name, status, output in results:
        print(f"\n[{name}] Status: {status.value}")
        print("-" * 40)
        # Show last 500 chars of output
        print(output[-500:] if len(output) > 500 else output)


async def interleaved_monitoring() -> None:
    """
    Demonstrate monitoring multiple sessions with interleaved checks.

    This pattern is useful when you need fine-grained control over
    multiple sessions without blocking on any single one.
    """
    from doeff_agents import launch_session, monitor_session, stop_session

    configs = [
        ("Task 1: List directory contents", "list-dir"),
        ("Task 2: Show current date", "show-date"),
    ]

    sessions = []
    session_names = []

    print("=" * 60)
    print("Interleaved monitoring of multiple sessions")
    print("=" * 60)

    # Launch all sessions
    for prompt, suffix in configs:
        config = LaunchConfig(
            agent_type=AgentType.CLAUDE,
            work_dir=Path.cwd(),
            prompt=prompt,
        )
        name = f"interleaved-{suffix}-{int(time.time())}"
        session = launch_session(name, config)
        sessions.append(session)
        session_names.append(suffix)
        print(f"Launched: {suffix}")

    try:
        # Monitor all sessions until all are terminal
        start_time = time.time()
        timeout = 120

        while not all(s.is_terminal for s in sessions):
            if time.time() - start_time > timeout:
                print("Timeout reached!")
                break

            # Check each session
            for session, name in zip(sessions, session_names, strict=False):
                if not session.is_terminal:
                    new_status = monitor_session(session)
                    if new_status:
                        print(f"[{name}] Status: {new_status.value}")

            # Small delay to avoid busy-waiting
            await asyncio.sleep(0.5)

        # Show final results
        print("\n" + "-" * 40)
        for session, name in zip(sessions, session_names, strict=False):
            print(f"[{name}] Final: {session.status.value}")

    finally:
        # Clean up all sessions
        for session in sessions:
            stop_session(session)
        print("\nAll sessions stopped.")


async def main() -> None:
    """Run all async examples."""
    import shutil

    if not shutil.which("claude"):
        print("Warning: Claude CLI not found.")
        print("Install with: npm install -g @anthropic/claude-code")
        return

    # Run single session example
    print("\n" + "=" * 60)
    print("Example 1: Single Async Session")
    print("=" * 60)
    await run_single_async_session()

    # Run parallel sessions example
    # (Uncomment to run - uses more resources)
    # print("\n")
    # await run_parallel_sessions()

    # Run interleaved monitoring example
    # (Uncomment to run)
    # print("\n")
    # await interleaved_monitoring()


if __name__ == "__main__":
    asyncio.run(main())
