#!/usr/bin/env python3
"""
Status Callbacks Example - Using doeff Effects API

This example demonstrates using slog effects to track and react to
session events instead of traditional callbacks.

With the effects-based approach:
- Status changes are logged via slog effects
- PR URL detection is logged via slog effects  
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
from datetime import datetime, timezone
from pathlib import Path

from doeff import AsyncRuntime, do
from doeff.effects.writer import slog
from doeff_preset import preset_handlers

from doeff_agents import (
    AgentType,
    Capture,
    CeskMockSessionScript,
    Launch,
    LaunchConfig,
    Monitor,
    Observation,
    SessionHandle,
    SessionStatus,
    Sleep,
    Stop,
    agent_effectful_handlers,
    configure_mock_session,
    mock_agent_handlers,
)


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
    pr_url: str | None = None
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
    yield slog(step="session_start", session_name=session_name, timestamp=datetime.now(timezone.utc).isoformat())
    
    handle: SessionHandle = yield Launch(session_name, config)
    yield slog(step="launched", pane_id=handle.pane_id)
    
    previous_status = SessionStatus.PENDING
    final_status = SessionStatus.PENDING
    pr_url: str | None = None
    iteration = 0
    
    try:
        for iteration in range(60):
            observation: Observation = yield Monitor(handle)
            final_status = observation.status
            
            # Log status changes (equivalent to on_status_change callback)
            if observation.status != previous_status:
                yield slog(
                    step="status_change",
                    event_type="status_change",
                    old_status=previous_status.value,
                    new_status=observation.status.value,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                
                # Log specific status events
                if observation.status == SessionStatus.BLOCKED:
                    yield slog(
                        step="notification",
                        event_type="blocked",
                        msg=f"Agent waiting for input ({iteration}s)",
                    )
                elif observation.status == SessionStatus.BLOCKED_API:
                    yield slog(
                        step="alert",
                        event_type="rate_limited",
                        msg="Hit API rate limit",
                        severity="warning",
                    )
                elif observation.status == SessionStatus.FAILED:
                    output = yield Capture(handle, lines=20)
                    yield slog(
                        step="alert",
                        event_type="failed",
                        msg="Session failed",
                        severity="error",
                        output_snippet=output[-300:] if output else None,
                    )
                elif observation.status == SessionStatus.DONE:
                    yield slog(
                        step="success",
                        event_type="completed",
                        msg=f"Session completed ({iteration}s)",
                    )
                
                previous_status = observation.status
            
            # Log PR detection (equivalent to on_pr_detected callback)
            if observation.pr_url and not pr_url:
                pr_url = observation.pr_url
                yield slog(
                    step="pr_created",
                    event_type="pr_detected",
                    pr_url=pr_url,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            
            if observation.is_terminal:
                break
            
            yield Sleep(1.0)
        
        # Final output capture
        output = yield Capture(handle, lines=50)
        yield slog(
            step="session_end",
            session_name=session_name,
            final_status=final_status.value,
            pr_url=pr_url,
            duration_iterations=iteration,
        )
        
        return {
            "session_name": session_name,
            "final_status": final_status.value,
            "pr_url": pr_url,
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
        step="collector_start",
        event_type="collection_started",
        session_name=session_name,
    )
    
    result = yield from monitored_session_workflow(session_name, config)
    
    yield slog(
        step="collector_end",
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
        step="notifier_start",
        event_type="notification",
        channel="monitoring",
        msg=f"Starting session: {session_name}",
    )
    
    handle: SessionHandle = yield Launch(session_name, config)
    previous_status = SessionStatus.PENDING
    final_status = SessionStatus.PENDING
    
    try:
        for iteration in range(60):
            observation = yield Monitor(handle)
            final_status = observation.status
            elapsed = time.time() - start_time
            
            if observation.status != previous_status:
                # Notification-style logging
                if observation.status == SessionStatus.BLOCKED:
                    yield slog(
                        step="notification",
                        channel="alerts",
                        severity="info",
                        msg=f"[{session_name}] Agent waiting for input ({elapsed:.0f}s)",
                    )
                elif observation.status == SessionStatus.BLOCKED_API:
                    yield slog(
                        step="notification",
                        channel="alerts",
                        severity="warning",
                        msg=f"[{session_name}] Hit API rate limit ({elapsed:.0f}s)",
                    )
                elif observation.status == SessionStatus.FAILED:
                    output = yield Capture(handle, lines=10)
                    yield slog(
                        step="notification",
                        channel="alerts",
                        severity="error",
                        msg=f"[{session_name}] Session FAILED ({elapsed:.0f}s)",
                        output_snippet=output[-200:] if output else None,
                    )
                elif observation.status == SessionStatus.DONE:
                    yield slog(
                        step="notification",
                        channel="success",
                        severity="info",
                        msg=f"[{session_name}] Session completed ({elapsed:.0f}s)",
                    )
                
                previous_status = observation.status
            
            if observation.pr_url:
                yield slog(
                    step="notification",
                    channel="prs",
                    severity="info",
                    msg=f"[{session_name}] PR Created: {observation.pr_url}",
                )
            
            if observation.is_terminal:
                break
            
            yield Sleep(1.0)
        
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
    
    initial_store = {}
    configure_mock_session(
        initial_store,
        session_name,
        CeskMockSessionScript([
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

    handlers = {
        **preset_handlers(),
        **mock_agent_handlers(),
    }
    runtime = AsyncRuntime(handlers=handlers, initial_store=initial_store)

    result = await runtime.run(monitored_session_workflow(session_name, config))
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
    print("      step='status_change',")
    print("      old_status=old.value,")
    print("      new_status=new.value,")
    print("  )")
    print()
    print("Benefits:")
    print("  - Pure effects, no side effects")
    print("  - Structured data, easily queryable")
    print("  - Accumulated in runtime log")
    print("  - Testable with mock handlers")
    print("  - Composable with preset_handlers for display")


async def run_with_real_tmux() -> None:
    """Run with AsyncRuntime and real tmux."""
    import shutil

    if not shutil.which("claude"):
        print("Claude CLI not available, skipping real tmux example")
        return

    print("\n" + "=" * 60)
    print("Running with AsyncRuntime + real tmux")
    print("=" * 60)

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Show the current date and time.",
    )

    session_name = f"notifier-{int(time.time())}"

    handlers = {
        **preset_handlers(),
        **agent_effectful_handlers(),
    }
    runtime = AsyncRuntime(handlers=handlers)

    result = await runtime.run(notification_workflow(session_name, config))
    print(f"\nResult: {result}")


async def main() -> None:
    """Run all examples."""
    await run_monitored_session()
    simulate_events_demo()

    # Uncomment to run with real tmux
    # await run_with_real_tmux()


if __name__ == "__main__":
    asyncio.run(main())
