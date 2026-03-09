"""Tmux session operations with typed errors and pane tracking."""

import os
import re
import subprocess
from datetime import datetime, timezone

from .session_backend import SessionBackend, SessionConfig, SessionInfo


class TmuxError(Exception):
    """Base exception for tmux operations."""


class TmuxNotAvailableError(TmuxError):
    """Raised when tmux is not installed."""


class SessionNotFoundError(TmuxError):
    """Raised when a tmux session doesn't exist."""


class SessionAlreadyExistsError(TmuxError):
    """Raised when trying to create a session that already exists."""


# ANSI escape sequence pattern for stripping
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return ANSI_PATTERN.sub("", text)


class TmuxSessionBackend(SessionBackend):
    """Session backend implementation backed by tmux."""

    def _ensure_tmux_available(self) -> None:
        if not self.is_available():
            raise TmuxNotAvailableError("tmux is not installed or not in PATH")

    def is_available(self) -> bool:
        result = subprocess.run(["tmux", "-V"], check=False, capture_output=True)
        return result.returncode == 0

    def is_inside_session(self) -> bool:
        return os.environ.get("TMUX") is not None

    def has_session(self, name: str) -> bool:
        self._ensure_tmux_available()
        result = subprocess.run(
            ["tmux", "has-session", "-t", name],
            check=False,
            capture_output=True,
        )
        return result.returncode == 0

    def new_session(self, cfg: SessionConfig) -> SessionInfo:
        self._ensure_tmux_available()
        if self.has_session(cfg.session_name):
            raise SessionAlreadyExistsError(f"Session '{cfg.session_name}' already exists")

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

    def send_keys(
        self,
        target: str,
        keys: str,
        *,
        literal: bool = True,
        enter: bool = True,
    ) -> None:
        self._ensure_tmux_available()
        args = ["tmux", "send-keys", "-t", target]
        if literal:
            args.extend(["-l", keys])
        else:
            args.append(keys)
        subprocess.run(args, check=True)

        if enter:
            subprocess.run(["tmux", "send-keys", "-t", target, "Enter"], check=True)

    def capture_pane(
        self,
        target: str,
        lines: int = 100,
        *,
        strip_ansi_codes: bool = True,
    ) -> str:
        self._ensure_tmux_available()
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

    def kill_session(self, session: str) -> None:
        self._ensure_tmux_available()
        subprocess.run(["tmux", "kill-session", "-t", session], check=True)

    def attach_session(self, session: str) -> None:
        self._ensure_tmux_available()
        if self.is_inside_session():
            subprocess.run(["tmux", "switch-client", "-t", session], check=True)
        else:
            subprocess.run(["tmux", "attach-session", "-t", session], check=True)

    def list_sessions(self) -> list[str]:
        self._ensure_tmux_available()
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            if "no server running" in result.stderr:
                return []
            raise TmuxError(f"Failed to list sessions: {result.stderr}")
        return [session for session in result.stdout.strip().split("\n") if session]


_DEFAULT_BACKEND = TmuxSessionBackend()


def get_default_backend() -> TmuxSessionBackend:
    """Return the process-wide default tmux backend instance."""
    return _DEFAULT_BACKEND


def is_tmux_available() -> bool:
    return _DEFAULT_BACKEND.is_available()


def is_inside_tmux() -> bool:
    return _DEFAULT_BACKEND.is_inside_session()


def has_session(name: str) -> bool:
    return _DEFAULT_BACKEND.has_session(name)


def new_session(cfg: SessionConfig) -> SessionInfo:
    return _DEFAULT_BACKEND.new_session(cfg)


def send_keys(target: str, keys: str, *, literal: bool = True, enter: bool = True) -> None:
    _DEFAULT_BACKEND.send_keys(target, keys, literal=literal, enter=enter)


def capture_pane(target: str, lines: int = 100, *, strip_ansi_codes: bool = True) -> str:
    return _DEFAULT_BACKEND.capture_pane(target, lines, strip_ansi_codes=strip_ansi_codes)


def kill_session(session: str) -> None:
    _DEFAULT_BACKEND.kill_session(session)


def attach_session(session: str) -> None:
    _DEFAULT_BACKEND.attach_session(session)


def list_sessions() -> list[str]:
    return _DEFAULT_BACKEND.list_sessions()


__all__ = [
    "SessionAlreadyExistsError",
    "SessionConfig",
    "SessionInfo",
    "SessionNotFoundError",
    "TmuxError",
    "TmuxNotAvailableError",
    "TmuxSessionBackend",
    "attach_session",
    "capture_pane",
    "get_default_backend",
    "has_session",
    "is_inside_tmux",
    "is_tmux_available",
    "kill_session",
    "list_sessions",
    "new_session",
    "send_keys",
    "strip_ansi",
]
