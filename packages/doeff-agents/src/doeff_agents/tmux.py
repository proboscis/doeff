"""Tmux session operations with typed errors and pane tracking."""

import os
import re
import subprocess
import time
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

    def __init__(self, executable: str | os.PathLike[str] | None = None) -> None:
        self.executable = str(executable or "tmux")

    def _args(self, *args: str) -> list[str]:
        return [self.executable, *args]

    def _ensure_tmux_available(self) -> None:
        if not self.is_available():
            raise TmuxNotAvailableError(f"tmux is not available: {self.executable}")

    def is_available(self) -> bool:
        result = subprocess.run(self._args("-V"), check=False, capture_output=True)
        return result.returncode == 0

    def is_inside_session(self) -> bool:
        return os.environ.get("TMUX") is not None

    def has_session(self, name: str) -> bool:
        self._ensure_tmux_available()
        result = subprocess.run(
            self._args("has-session", "-t", name),
            check=False,
            capture_output=True,
        )
        return result.returncode == 0

    def new_session(self, cfg: SessionConfig) -> SessionInfo:
        self._ensure_tmux_available()
        if self.has_session(cfg.session_name):
            raise SessionAlreadyExistsError(f"Session '{cfg.session_name}' already exists")

        args = self._args("new-session", "-d", "-s", cfg.session_name, "-P", "-F", "#D")
        if cfg.work_dir:
            args.extend(["-c", str(cfg.work_dir)])
        if cfg.window_name:
            args.extend(["-n", cfg.window_name])
        # Propagate env vars into the pane shell. Setting them on the
        # tmux client subprocess doesn't reach the pane (the daemon spawns
        # the shell), so we must use `-e KEY=VAL`.
        if cfg.env:
            for key, value in cfg.env.items():
                args.extend(["-e", f"{key}={value}"])

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
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
        if literal and keys:
            self._paste_literal(target, keys)
        elif keys:
            args = self._args("send-keys", "-t", target)
            if literal:
                args.extend(["-l", keys])
            else:
                args.append(keys)
            subprocess.run(args, check=True)

        if enter:
            if literal and keys:
                # Claude/Codex redraw the input box after large pasted prompts.
                # Pressing Enter immediately after the paste can be swallowed by
                # that redraw, leaving "[Pasted text ...]" in the prompt.
                time.sleep(1.0)
            subprocess.run(self._args("send-keys", "-t", target, "Enter"), check=True)
            if literal and keys:
                self._confirm_literal_prompt_submitted(target)

    def _paste_literal(self, target: str, text: str) -> None:
        buffer_name = (
            f"doeff-agents-{os.getpid()}-"
            f"{re.sub(r'[^A-Za-z0-9_]+', '_', target)}"
        )
        subprocess.run(
            self._args("set-buffer", "-b", buffer_name, text),
            check=True,
        )
        try:
            subprocess.run(
                self._args("paste-buffer", "-b", buffer_name, "-t", target),
                check=True,
            )
        finally:
            subprocess.run(
                self._args("delete-buffer", "-b", buffer_name),
                check=False,
                capture_output=True,
            )

    def _confirm_literal_prompt_submitted(self, target: str) -> None:
        time.sleep(1.2)
        output = self.capture_pane(target, 20)
        if _output_has_unsubmitted_paste_input(output):
            subprocess.run(self._args("send-keys", "-t", target, "Enter"), check=True)

    def capture_pane(
        self,
        target: str,
        lines: int = 100,
        *,
        strip_ansi_codes: bool = True,
    ) -> str:
        self._ensure_tmux_available()
        result = subprocess.run(
            self._args("capture-pane", "-t", target, "-p", "-S", f"-{lines}"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        output = result.stdout
        if strip_ansi_codes:
            output = strip_ansi(output)
        return output

    def kill_session(self, session: str) -> None:
        self._ensure_tmux_available()
        subprocess.run(self._args("kill-session", "-t", session), check=True)

    def attach_session(self, session: str) -> None:
        self._ensure_tmux_available()
        if self.is_inside_session():
            subprocess.run(self._args("switch-client", "-t", session), check=True)
        else:
            subprocess.run(self._args("attach-session", "-t", session), check=True)

    def list_sessions(self) -> list[str]:
        self._ensure_tmux_available()
        result = subprocess.run(
            self._args("list-sessions", "-F", "#S"),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode != 0:
            if "no server running" in result.stderr:
                return []
            raise TmuxError(f"Failed to list sessions: {result.stderr}")
        return [session for session in result.stdout.strip().split("\n") if session]


def get_default_backend() -> TmuxSessionBackend:
    """Return a default tmux backend instance using `tmux` from PATH."""
    return TmuxSessionBackend()


def _output_has_unsubmitted_paste_input(output: str) -> bool:
    last_prompt_line = ""
    for line in output.splitlines()[-12:]:
        stripped = line.lstrip()
        if stripped.startswith(("❯", "›")):  # noqa: RUF001
            last_prompt_line = stripped
    return "[Pasted text" in last_prompt_line


def is_tmux_available() -> bool:
    return get_default_backend().is_available()


def is_inside_tmux() -> bool:
    return get_default_backend().is_inside_session()


def has_session(name: str) -> bool:
    return get_default_backend().has_session(name)


def new_session(cfg: SessionConfig) -> SessionInfo:
    return get_default_backend().new_session(cfg)


def send_keys(target: str, keys: str, *, literal: bool = True, enter: bool = True) -> None:
    get_default_backend().send_keys(target, keys, literal=literal, enter=enter)


def capture_pane(target: str, lines: int = 100, *, strip_ansi_codes: bool = True) -> str:
    return get_default_backend().capture_pane(target, lines, strip_ansi_codes=strip_ansi_codes)


def kill_session(session: str) -> None:
    get_default_backend().kill_session(session)


def attach_session(session: str) -> None:
    get_default_backend().attach_session(session)


def list_sessions() -> list[str]:
    return get_default_backend().list_sessions()


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
