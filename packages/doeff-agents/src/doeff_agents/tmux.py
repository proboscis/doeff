"""Tmux session operations with typed errors and pane tracking."""

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class TmuxError(Exception):
    """Base exception for tmux operations."""


class TmuxNotAvailableError(TmuxError):
    """Raised when tmux is not installed."""


class SessionNotFoundError(TmuxError):
    """Raised when a tmux session doesn't exist."""


class SessionAlreadyExistsError(TmuxError):
    """Raised when trying to create a session that already exists."""


@dataclass(frozen=True)
class SessionConfig:
    """Configuration for creating a tmux session."""

    session_name: str
    work_dir: Path | None = None
    env: dict[str, str] | None = None
    window_name: str | None = None


@dataclass(frozen=True)
class SessionInfo:
    """Information about a created tmux session."""

    session_name: str
    pane_id: str  # Track pane ID for reliable targeting
    created_at: datetime


# ANSI escape sequence pattern for stripping
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return ANSI_PATTERN.sub("", text)


def _ensure_tmux_available() -> None:
    """Check that tmux is available, raise if not."""
    if not is_tmux_available():
        raise TmuxNotAvailableError("tmux is not installed or not in PATH")


def is_tmux_available() -> bool:
    """Check if tmux is installed."""
    result = subprocess.run(["tmux", "-V"], check=False, capture_output=True)
    return result.returncode == 0


def is_inside_tmux() -> bool:
    """Check if we're inside a tmux session."""
    return os.environ.get("TMUX") is not None


def has_session(name: str) -> bool:
    """Check if a tmux session exists."""
    _ensure_tmux_available()
    result = subprocess.run(["tmux", "has-session", "-t", name], check=False, capture_output=True)
    return result.returncode == 0


def new_session(cfg: SessionConfig) -> SessionInfo:
    """Create a new detached tmux session.

    Returns:
        SessionInfo with pane_id for reliable targeting
    """
    _ensure_tmux_available()

    if has_session(cfg.session_name):
        raise SessionAlreadyExistsError(f"Session '{cfg.session_name}' already exists")

    # Create session and get pane ID in one command
    args = ["tmux", "new-session", "-d", "-s", cfg.session_name, "-P", "-F", "#{pane_id}"]

    if cfg.work_dir:
        args.extend(["-c", str(cfg.work_dir)])
    if cfg.window_name:
        args.extend(["-n", cfg.window_name])

    env = dict(os.environ)
    if cfg.env:
        env.update(cfg.env)

    result = subprocess.run(args, env=env, capture_output=True, text=True, check=True)
    pane_id = result.stdout.strip()

    return SessionInfo(
        session_name=cfg.session_name,
        pane_id=pane_id,
        created_at=datetime.now(timezone.utc),
    )


def send_keys(target: str, keys: str, *, literal: bool = True, enter: bool = True) -> None:
    """Send keys to a tmux pane.

    Args:
        target: Session name or pane ID (e.g., "my-session" or "%42")
        keys: The keys/text to send
        literal: If True, send keys literally (no special key interpretation)
        enter: If True, press Enter after sending keys
    """
    _ensure_tmux_available()
    args = ["tmux", "send-keys", "-t", target]
    if literal:
        args.extend(["-l", keys])
    else:
        args.append(keys)
    subprocess.run(args, check=True)

    if enter:
        subprocess.run(["tmux", "send-keys", "-t", target, "Enter"], check=True)


def capture_pane(target: str, lines: int = 100, *, strip_ansi_codes: bool = True) -> str:
    """Capture the content of a tmux pane.

    Args:
        target: Session name or pane ID
        lines: Number of lines to capture
        strip_ansi_codes: If True, remove ANSI escape sequences
    """
    _ensure_tmux_available()
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", target, "-p", "-S", f"-{lines}"],
        capture_output=True,
        text=True,
        check=True,
    )
    output = result.stdout
    if strip_ansi_codes:
        output = strip_ansi(output)
    return output


def kill_session(session: str) -> None:
    """Kill a tmux session."""
    _ensure_tmux_available()
    subprocess.run(["tmux", "kill-session", "-t", session], check=True)


def attach_session(session: str) -> None:
    """Attach to a tmux session.

    If already inside tmux, switches to the target session instead of attaching.
    """
    _ensure_tmux_available()
    if is_inside_tmux():
        # Switch to session when already inside tmux
        subprocess.run(["tmux", "switch-client", "-t", session], check=True)
    else:
        # Attach from outside tmux (blocks until detached)
        subprocess.run(["tmux", "attach-session", "-t", session], check=True)


def list_sessions() -> list[str]:
    """List all tmux session names.

    Raises:
        TmuxNotAvailableError: If tmux is not installed
    """
    _ensure_tmux_available()
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        check=False, capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # No sessions exist (not an error)
        if "no server running" in result.stderr:
            return []
        raise TmuxError(f"Failed to list sessions: {result.stderr}")
    return [s for s in result.stdout.strip().split("\n") if s]
