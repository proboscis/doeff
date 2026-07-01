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


# Callback type for status change events
OnStatusChange = Callable[[SessionStatus, SessionStatus, str | None], None]


CODEX_IDLE_DONE_SECONDS = 2.0


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


def has_codex_active_marker(output: str) -> bool:
    """Return True when Codex is visibly inside an active turn."""
    lines = "\n".join(output.split("\n")[-30:]).lower()
    patterns = [
        "working (",
        "thinking",
        "esc to interrupt",
        "ctrl + t to view transcript",
    ]
    return any(p in lines for p in patterns)


def has_claude_active_marker(output: str) -> bool:
    """Return True when Claude Code is visibly inside an active turn."""
    lines = "\n".join(output.split("\n")[-30:]).lower()
    patterns = [
        "esc to interrupt",
        "thinking with",
    ]
    return any(p in lines for p in patterns)


def has_claude_background_shell_marker(output: str) -> bool:
    """Return True when Claude Code has a background shell task still running."""
    recent_lines = output.splitlines()[-40:]
    recent = "\n".join(recent_lines).lower()
    if "shell still running" in recent:
        return True
    for line in recent_lines:
        lowered = line.lower()
        if "shell" not in lowered:
            continue
        if "ctrl+t" in lowered and "hide task" in lowered:
            return True
        if re.search(r"\b\d+\s+shells?\b", lowered) and "running" in recent:
            return True
    return False


def has_codex_idle_prompt(output: str) -> bool:
    """Return True when Codex shows its idle prompt/status footer."""
    has_prompt = any(line.startswith("› ") for line in output.splitlines())  # noqa: RUF001
    has_model_status = any("gpt-" in line and "·" in line for line in output.splitlines())
    return bool(has_prompt and has_model_status)


def is_codex_turn_complete(
    output: str,
    state: MonitorState,
    *,
    output_changed: bool,
    idle_done_seconds: float = CODEX_IDLE_DONE_SECONDS,
) -> bool:
    """Detect Codex's normal post-turn idle screen.

    Codex often finishes a turn and returns to its interactive prompt without
    printing generic phrases like "task completed successfully". In tmux the
    process is still alive, so process-exit detection is not enough. Treat a
    stable Codex prompt with no active-turn marker as a completed turn.
    """
    if not has_codex_idle_prompt(output):
        return False
    if has_codex_active_marker(output):
        return False
    if output_changed:
        return False
    idle_seconds = (datetime.now(timezone.utc) - state.last_output_at).total_seconds()
    return idle_seconds >= idle_done_seconds


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


def detect_status(
    output: str,
    state: MonitorState,
    output_changed: bool,
    has_prompt: bool,
) -> SessionStatus | None:
    """Detect session status from output.

    Detection order:
    1. API limit patterns → BlockedAPI
    2. Stable idle Codex prompt → Blocked
    3. Claude active-turn marker → Running
    4. Claude background shell marker → Running
    5. Agent exited → Exited (shell prompt showing)
    6. Output changing → Running
    7. Output stable + prompt → Blocked
    8. Otherwise → None (no change)

    This function deliberately does not derive DONE/FAILED from terminal text.
    Workflow success is decided by the schema-validated structured result.
    """
    status = None
    if is_api_limited(output):
        status = SessionStatus.BLOCKED_API
    elif is_codex_turn_complete(output, state, output_changed=output_changed):
        status = SessionStatus.BLOCKED
    elif has_claude_active_marker(output) or has_claude_background_shell_marker(output):
        status = SessionStatus.RUNNING
    elif is_agent_exited(output):
        status = SessionStatus.EXITED
    elif output_changed:
        status = SessionStatus.RUNNING
    elif has_prompt:
        status = SessionStatus.BLOCKED
    return status
