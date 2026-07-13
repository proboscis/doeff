#!/usr/bin/env python3
"""
Status Callbacks Example - Using doeff Effects API

This example demonstrates using slog effects to track and react to
session events instead of traditional callbacks.

With the effects-based approach:
- Status changes are logged via slog effects
- Event collection is done through the runtime's log accumulation
- All events are structured and queryable

Benefits:
- Pure effects, no side effects in callbacks
- Structured logging with slog
- Testable with mock handlers
- Log accumulation through the runtime
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from _runtime import run_program
from doeff_agents import (
    AgentType,
    Capture,
    Launch,
    LaunchConfig,
    MockSessionScript,
    Monitor,
    Observation,
    SessionHandle,
    SessionStatus,
    Stop,
    agent_effectful_handlers,
    configure_mock_session,
    mock_agent_handlers,
)
from doeff_time import Delay, GetTime

from doeff import do, slog

# =============================================================================
# Event Types for Structured Logging
# =============================================================================


@dataclass
class SessionEvent:
    """Represents a session event (for post-processing logs)."""

    timestamp: datetime
    event_type: str
    old_status: SessionStatus | None = None
    new_status: SessionStatus | None = None
    output_snippet: str | None = None


# =============================================================================
# Effects-based Workflows with Structured Logging
# =============================================================================


@do
def monitored_session_workflow(session_name: str, config: LaunchConfig):
    """Run a session with comprehensive slog-based monitoring.

    Instead of callbacks, we yield slog effects for every event.
    The runtime accumulates these logs for later analysis.
    """
    start_time: datetime = yield GetTime()
    yield slog("session_start", session_name=session_name, timestamp=start_time.isoformat())

    handle: SessionHandle = yield Launch(
        session_name,
        agent_type=config.agent_type,
        work_dir=config.work_dir,
        prompt=config.prompt,
    )
    yield slog("launched", session_id=handle.session_id)

    previous_status = SessionStatus.PENDING
    final_status = SessionStatus.PENDING
    iteration = 0

    try:
        for iteration in range(60):
            observation: Observation = yield Monitor(handle)
            final_status = observation.status

            # Log status changes (equivalent to on_status_change callback)
            if observation.status != previous_status:
                status_change_time: datetime = yield GetTime()
                yield slog(
                    "status_change",
                    event_type="status_change",
                    old_status=previous_status.value,
                    new_status=observation.status.value,
                    timestamp=status_change_time.isoformat(),
                )

                # Log specific status events
                if observation.status == SessionStatus.BLOCKED:
                    yield slog(
                        msg=f"Agent waiting for input ({iteration}s)",
                        step="notification",
                        event_type="blocked",
                    )
                elif observation.status == SessionStatus.BLOCKED_API:
                    yield slog(
                        msg="Hit API rate limit",
                        step="alert",
                        event_type="rate_limited",
                        severity="warning",
                    )
                elif observation.status == SessionStatus.FAILED:
                    output = yield Capture(handle, lines=20)
                    yield slog(
                        msg="Session failed",
                        step="alert",
                        event_type="failed",
                        severity="error",
                        output_snippet=output[-300:] if output else None,
                    )
                elif observation.status == SessionStatus.DONE:
                    yield slog(
                        msg=f"Session completed ({iteration}s)",
                        step="success",
                        event_type="completed",
                    )

                previous_status = observation.status

            if observation.is_terminal:
                break

            yield Delay(1.0)

        # Final output capture
        output = yield Capture(handle, lines=50)
        yield slog(
            "session_end",
            session_name=session_name,
            final_status=final_status.value,
            duration_iterations=iteration,
        )

        return {
            "session_name": session_name,
            "final_status": final_status.value,
            "output": output,
            "iterations": iteration,
        }

    finally:
        yield Stop(handle)


@do
def event_collecting_workflow(session_name: str, config: LaunchConfig):
    """Workflow that collects events for post-run analysis.

    Uses slog with structured data that can be extracted from
    the runtime's accumulated log.
    """
    yield slog(
        "collector_start",
        event_type="collection_started",
        session_name=session_name,
    )

    result = yield monitored_session_workflow(session_name, config)

    yield slog(
        "collector_end",
        event_type="collection_complete",
        total_events="see_accumulated_log",
        final_status=result["final_status"],
    )

    return result


@do
def notification_workflow(session_name: str, config: LaunchConfig):
    """Workflow demonstrating notification-style events.

    In production, these slogs could be processed by a handler
    that sends actual notifications (Slack, email, etc.).
    """
    start_time = time.time()

    yield slog(
        msg=f"Starting session: {session_name}",
        step="notifier_start",
        event_type="notification",
        channel="monitoring",
    )

    handle: SessionHandle = yield Launch(
        session_name,
        agent_type=config.agent_type,
        work_dir=config.work_dir,
        prompt=config.prompt,
    )
    previous_status = SessionStatus.PENDING
    final_status = SessionStatus.PENDING

    try:
        for _iteration in range(60):
            observation = yield Monitor(handle)
            final_status = observation.status
            elapsed = time.time() - start_time

            if observation.status != previous_status:
                # Notification-style logging
                if observation.status == SessionStatus.BLOCKED:
                    yield slog(
                        msg=f"[{session_name}] Agent waiting for input ({elapsed:.0f}s)",
                        step="notification",
                        channel="alerts",
                        severity="info",
                    )
                elif observation.status == SessionStatus.BLOCKED_API:
                    yield slog(
                        msg=f"[{session_name}] Hit API rate limit ({elapsed:.0f}s)",
                        step="notification",
                        channel="alerts",
                        severity="warning",
                    )
                elif observation.status == SessionStatus.FAILED:
                    output = yield Capture(handle, lines=10)
                    yield slog(
                        msg=f"[{session_name}] Session FAILED ({elapsed:.0f}s)",
                        step="notification",
                        channel="alerts",
                        severity="error",
                        output_snippet=output[-200:] if output else None,
                    )
                elif observation.status == SessionStatus.DONE:
                    yield slog(
                        msg=f"[{session_name}] Session completed ({elapsed:.0f}s)",
                        step="notification",
                        channel="success",
                        severity="info",
                    )

                previous_status = observation.status

            if observation.is_terminal:
                break

            yield Delay(1.0)

        output = yield Capture(handle, lines=30)
        return {
            "session_name": session_name,
            "final_status": final_status.value,
            "output": output,
        }

    finally:
        yield Stop(handle)


# =============================================================================
# Demo Functions
# =============================================================================


async def run_monitored_session() -> None:
    """Run with mock handlers and show accumulated logs."""
    print("=" * 60)
    print("Status Callbacks via slog Effects")
    print("=" * 60)

    session_name = f"callbacks-{int(time.time())}"

    configure_mock_session(
        session_name,
        MockSessionScript(observations=[
            (SessionStatus.RUNNING, "Starting task..."),
            (SessionStatus.BLOCKED, "Need input..."),
            (SessionStatus.RUNNING, "Continuing..."),
            (SessionStatus.DONE, "Task completed! Created PR: https://github.com/user/repo/pull/123"),
        ]),
    )

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="List the current directory contents.",
    )

    result = await run_program(
        monitored_session_workflow(session_name, config),
        custom_handlers=mock_agent_handlers(),
    )
    print(f"\nResult: {result}")


def simulate_events_demo() -> None:
    """Show how slog effects replace traditional callbacks."""
    print("\n" + "=" * 60)
    print("Demo: slog Effects as Callbacks")
    print("=" * 60)

    print("\nTraditional callback approach:")
    print("  def on_status_change(old, new, output):")
    print("      print(f'{old} -> {new}')")
    print()
    print("Effects-based approach (what we use now):")
    print("  yield slog(")
    print("      'status_change',")
    print("      old_status=old.value,")
    print("      new_status=new.value,")
    print("  )")
    print()
    print("Benefits:")
    print("  - Pure effects, no side effects")
    print("  - Structured data, easily queryable")
    print("  - Accumulated in runtime log")
    print("  - Testable with mock handlers")
    print("  - Composable with the standard slog display stack")


async def run_with_real_tmux() -> None:
    """Run with real tmux."""
    import shutil

    if not shutil.which("claude"):
        print("Claude CLI not available, skipping real tmux example")
        return

    print("\n" + "=" * 60)
    print("Running with real tmux")
    print("=" * 60)

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Show the current date and time.",
    )

    session_name = f"notifier-{int(time.time())}"

    result = await run_program(
        notification_workflow(session_name, config),
        custom_handlers=agent_effectful_handlers(),
    )
    print(f"\nResult: {result}")


async def main() -> None:
    """Run all examples."""
    await run_monitored_session()
    simulate_events_demo()

    # Uncomment to run with real tmux
    # await run_with_real_tmux()


if __name__ == "__main__":
    asyncio.run(main())
