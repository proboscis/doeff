#!/usr/bin/env python3
"""
Status Callbacks Example

This example demonstrates using callbacks to react to session events:
- Status changes (RUNNING, BLOCKED, DONE, etc.)
- PR URL detection (when agent creates a pull request)

Callbacks are useful for:
- Logging and monitoring
- Sending notifications (Slack, email, etc.)
- Triggering automated workflows
- Updating dashboards
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from doeff_agents import (
    AgentType,
    LaunchConfig,
    SessionStatus,
    monitor_session,
    session_scope,
)

# =============================================================================
# Example 1: Simple Logging Callbacks
# =============================================================================


def simple_status_callback(
    old_status: SessionStatus,
    new_status: SessionStatus,
    output: str | None,
) -> None:
    """Log status changes with timestamps."""
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{timestamp}] Status: {old_status.value} -> {new_status.value}")


def simple_pr_callback(url: str) -> None:
    """Log when a PR is created."""
    print(f"PR Created: {url}")


# =============================================================================
# Example 2: Event Collector
# =============================================================================


@dataclass
class SessionEvent:
    """Represents a session event."""

    timestamp: datetime
    event_type: str
    old_status: SessionStatus | None = None
    new_status: SessionStatus | None = None
    pr_url: str | None = None
    output_snippet: str | None = None


@dataclass
class EventCollector:
    """Collects and stores session events for later analysis."""

    events: list[SessionEvent] = field(default_factory=list)

    def on_status_change(
        self,
        old_status: SessionStatus,
        new_status: SessionStatus,
        output: str | None,
    ) -> None:
        """Record a status change event."""
        event = SessionEvent(
            timestamp=datetime.now(timezone.utc),
            event_type="status_change",
            old_status=old_status,
            new_status=new_status,
            output_snippet=output[-200:] if output else None,
        )
        self.events.append(event)
        print(f"Collected event: {old_status.value} -> {new_status.value}")

    def on_pr_detected(self, url: str) -> None:
        """Record a PR detection event."""
        event = SessionEvent(
            timestamp=datetime.now(timezone.utc),
            event_type="pr_detected",
            pr_url=url,
        )
        self.events.append(event)
        print(f"Collected PR event: {url}")

    def summary(self) -> dict:
        """Generate a summary of collected events."""
        status_changes = [e for e in self.events if e.event_type == "status_change"]
        pr_events = [e for e in self.events if e.event_type == "pr_detected"]

        return {
            "total_events": len(self.events),
            "status_changes": len(status_changes),
            "pr_detections": len(pr_events),
            "pr_urls": [e.pr_url for e in pr_events],
            "final_status": status_changes[-1].new_status.value if status_changes else "unknown",
            "duration": (
                (self.events[-1].timestamp - self.events[0].timestamp).total_seconds()
                if len(self.events) > 1
                else 0
            ),
        }


# =============================================================================
# Example 3: Notification Handler
# =============================================================================


class NotificationHandler:
    """
    Handle notifications for important events.

    In a real application, these methods would send actual notifications
    via Slack, email, webhooks, etc.
    """

    def __init__(self, session_name: str) -> None:
        self.session_name = session_name
        self.start_time = time.time()

    def on_status_change(
        self,
        old_status: SessionStatus,
        new_status: SessionStatus,
        output: str | None,
    ) -> None:
        """Send notifications for important status changes."""
        elapsed = time.time() - self.start_time

        # Notify on important transitions
        if new_status == SessionStatus.BLOCKED:
            self._notify_blocked(elapsed)
        elif new_status == SessionStatus.BLOCKED_API:
            self._notify_rate_limited(elapsed)
        elif new_status == SessionStatus.FAILED:
            self._notify_failed(elapsed, output)
        elif new_status == SessionStatus.DONE:
            self._notify_completed(elapsed)

    def on_pr_detected(self, url: str) -> None:
        """Send notification when PR is created."""
        self._notify_pr(url)

    def _notify_blocked(self, elapsed: float) -> None:
        print(f"NOTIFICATION: [{self.session_name}] Agent waiting for input ({elapsed:.0f}s)")
        # In production: send to Slack, etc.

    def _notify_rate_limited(self, elapsed: float) -> None:
        print(f"ALERT: [{self.session_name}] Hit API rate limit ({elapsed:.0f}s)")
        # In production: alert on-call, pause other sessions, etc.

    def _notify_failed(self, elapsed: float, output: str | None) -> None:
        error_snippet = output[-300:] if output else "No output available"
        print(f"ALERT: [{self.session_name}] Session FAILED ({elapsed:.0f}s)")
        print(f"  Last output: {error_snippet}")
        # In production: create incident ticket, etc.

    def _notify_completed(self, elapsed: float) -> None:
        print(f"SUCCESS: [{self.session_name}] Session completed ({elapsed:.0f}s)")
        # In production: update dashboard, trigger CI/CD, etc.

    def _notify_pr(self, url: str) -> None:
        print(f"PR CREATED: [{self.session_name}] {url}")
        # In production: post to Slack channel, add reviewers, etc.


# =============================================================================
# Running the Examples
# =============================================================================


def run_with_simple_callbacks() -> None:
    """Run a session with simple logging callbacks."""
    print("=" * 60)
    print("Example: Simple Logging Callbacks")
    print("=" * 60)

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="List the current directory contents.",
    )

    session_name = f"callbacks-{int(time.time())}"

    with session_scope(session_name, config) as session:
        for _ in range(60):  # Monitor for up to 60 seconds
            monitor_session(
                session,
                on_status_change=simple_status_callback,
                on_pr_detected=simple_pr_callback,
            )

            if session.is_terminal:
                break

            time.sleep(1)

        print(f"\nFinal status: {session.status.value}")


def run_with_event_collector() -> None:
    """Run a session with event collection for analysis."""
    print("=" * 60)
    print("Example: Event Collector")
    print("=" * 60)

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Create a new file called hello.txt with some content.",
    )

    session_name = f"collector-{int(time.time())}"
    collector = EventCollector()

    with session_scope(session_name, config) as session:
        for _ in range(60):
            monitor_session(
                session,
                on_status_change=collector.on_status_change,
                on_pr_detected=collector.on_pr_detected,
            )

            if session.is_terminal:
                break

            time.sleep(1)

    # Print collected events summary
    print("\n" + "-" * 40)
    print("Event Summary:")
    summary = collector.summary()
    for key, value in summary.items():
        print(f"  {key}: {value}")


def run_with_notifications() -> None:
    """Run a session with notification handling."""
    print("=" * 60)
    print("Example: Notification Handler")
    print("=" * 60)

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Show the current date and time.",
    )

    session_name = f"notifier-{int(time.time())}"
    handler = NotificationHandler(session_name)

    with session_scope(session_name, config) as session:
        for _ in range(60):
            monitor_session(
                session,
                on_status_change=handler.on_status_change,
                on_pr_detected=handler.on_pr_detected,
            )

            if session.is_terminal:
                break

            time.sleep(1)

        print(f"\nFinal status: {session.status.value}")


if __name__ == "__main__":
    import shutil

    if not shutil.which("claude"):
        print("Warning: Claude CLI not found.")
        print("Install with: npm install -g @anthropic/claude-code")

        # Show a demo without actually running
        print("\n" + "=" * 60)
        print("Demo: Callback Types")
        print("=" * 60)

        # Simulate events
        print("\nSimulating events...")
        simple_status_callback(SessionStatus.PENDING, SessionStatus.BOOTING, None)
        simple_status_callback(SessionStatus.BOOTING, SessionStatus.RUNNING, None)
        simple_status_callback(SessionStatus.RUNNING, SessionStatus.DONE, "Task completed")
        simple_pr_callback("https://github.com/user/repo/pull/123")
    else:
        # Run actual examples
        run_with_simple_callbacks()
        # Uncomment to run other examples:
        # run_with_event_collector()
        # run_with_notifications()
