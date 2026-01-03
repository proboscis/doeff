"""Status monitoring and detection logic for agent sessions."""

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class SessionStatus(Enum):
    """Session lifecycle states.

    Transitions:
    - PENDING → BOOTING → RUNNING
    - RUNNING ↔ BLOCKED (waiting for input)
    - RUNNING → BLOCKED_API (rate limited)
    - RUNNING → DONE (completed successfully)
    - RUNNING → FAILED (error)
    - RUNNING → EXITED (agent process ended, shell prompt showing)
    - Any → STOPPED (explicitly killed by user)
    """

    PENDING = "pending"
    BOOTING = "booting"
    RUNNING = "running"
    BLOCKED = "blocked"  # Waiting for user input
    BLOCKED_API = "blocked_api"  # API rate limit
    DONE = "done"
    FAILED = "failed"
    EXITED = "exited"  # Agent process ended (shell prompt showing)
    STOPPED = "stopped"  # Explicitly killed by user


@dataclass
class MonitorState:
    """Tracks session state for change detection."""

    output_hash: str = ""
    last_output: str = ""
    last_output_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pr_url: str | None = None


# Callback type for status change events
OnStatusChange = Callable[[SessionStatus, SessionStatus, str | None], None]


def hash_content(output: str, skip_lines: int = 5) -> str:
    """Hash content excluding status bar (last N lines)."""
    lines = output.split("\n")
    if len(lines) > skip_lines:
        lines = lines[:-skip_lines]
    content = "\n".join(lines)
    return hashlib.md5(content.encode()).hexdigest()


def is_waiting_for_input(output: str, patterns: list[str] | None = None) -> bool:
    """Check if agent is waiting for user input.

    Args:
        output: Pane content
        patterns: Custom patterns (defaults to Claude patterns)
    """
    if patterns is None:
        patterns = [
            "No, and tell Claude what to do differently",
            "tell Claude what to do differently",
            "Type your message",  # Gemini
            "↵ send",
            "? for shortcuts",
            "accept edits",
            "bypass permissions",
            "shift+tab to cycle",
            "Esc to cancel",
            "to show all projects",
        ]
    return any(p in output for p in patterns)


def is_agent_exited(output: str, ui_patterns: list[str] | None = None) -> bool:
    """Check if agent process exited (shell prompt showing).

    Args:
        output: Pane content
        ui_patterns: Agent UI patterns that indicate it's still running
    """
    if ui_patterns is None:
        ui_patterns = [
            "↵ send",
            "accept edits",
            "? for shortcuts",
            "tell Claude what to do differently",
            "tokens",
            "Esc to cancel",
            "to show all projects",
        ]

    # If any agent UI pattern is present, agent is still running
    if any(p in output for p in ui_patterns):
        return False

    lines = [line.strip() for line in output.strip().split("\n") if line.strip()]
    if not lines:
        return False

    last_line = lines[-1]

    # Git prompt pattern
    if "git:(" in last_line and ")" in last_line:
        return True

    # Common shell prompt endings (unicode chars are intentional)
    shell_endings = ["$ ", "% ", "# ", "❯ ", "➜ "]  # noqa: RUF001
    return any(last_line.endswith(e) or last_line.rstrip().endswith(e[0]) for e in shell_endings)


def is_completed(output: str) -> bool:
    """Check if agent completed successfully."""
    # Check last 10 lines instead of 5 to account for UI chrome after completion
    lines = "\n".join(output.split("\n")[-10:]).lower()
    patterns = [
        "task completed successfully",
        "all tasks completed",
        "session ended",
        "goodbye",
    ]
    return any(p in lines for p in patterns)


def is_api_limited(output: str) -> bool:
    """Check if agent hit API rate limits."""
    lines = "\n".join(output.split("\n")[-30:]).lower()
    patterns = [
        "cost limit reached",
        "rate limit exceeded",
        "rate limit reached",
        "quota exceeded",
        "insufficient quota",
        "resource exhausted",
        "you've hit your limit",
        "/rate-limit-options",
        "stop and wait for limit to reset",
    ]
    return any(p in lines for p in patterns)


def is_failed(output: str) -> bool:
    """Check if agent failed."""
    lines = "\n".join(output.split("\n")[-10:]).lower()
    patterns = [
        "fatal error",
        "unrecoverable error",
        "agent crashed",
        "session terminated",
        "authentication failed",
    ]
    return any(p in lines for p in patterns)


def detect_status(  # noqa: PLR0911
    output: str,
    state: MonitorState,
    output_changed: bool,
    has_prompt: bool,
) -> SessionStatus | None:
    """Detect session status from output.

    Detection order (IMPORTANT: completion before exit check):
    1. Completion patterns → Done (even if shell prompt is visible)
    2. API limit patterns → BlockedAPI
    3. Error patterns → Failed
    4. Agent exited → Exited (shell prompt showing)
    5. Output changing → Running
    6. Output stable + prompt → Blocked
    7. Otherwise → None (no change)
    """
    # Check terminal states first (completion before exit!)
    if is_completed(output):
        return SessionStatus.DONE

    if is_api_limited(output):
        return SessionStatus.BLOCKED_API

    if is_failed(output):
        return SessionStatus.FAILED

    # Agent exited AFTER completion check (agent may show shell after saying goodbye)
    if is_agent_exited(output):
        return SessionStatus.EXITED

    if output_changed:
        return SessionStatus.RUNNING

    if has_prompt:
        return SessionStatus.BLOCKED

    return None


# PR URL detection
PR_URL_PATTERN = re.compile(
    r"https://(?:github\.com|gitlab\.com)/[^\s]+/(?:pull|merge_requests)/\d+"
)


def detect_pr_url(output: str) -> str | None:
    """Detect PR creation URL in output."""
    match = PR_URL_PATTERN.search(output)
    return match.group(0) if match else None
