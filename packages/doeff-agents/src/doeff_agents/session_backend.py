"""Backend-neutral terminal session transport primitives."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SessionConfig:
    """Configuration for creating a terminal-backed session."""

    session_name: str
    work_dir: Path | None = None
    env: dict[str, str] | None = None
    window_name: str | None = None


@dataclass(frozen=True)
class SessionInfo:
    """Information about a created terminal-backed session."""

    session_name: str
    pane_id: str
    created_at: datetime


class SessionBackend(Protocol):
    """Protocol for terminal multiplexers such as tmux or zellij."""

    def is_available(self) -> bool: ...

    def is_inside_session(self) -> bool: ...

    def has_session(self, name: str) -> bool: ...

    def new_session(self, cfg: SessionConfig) -> SessionInfo: ...

    def send_keys(
        self,
        target: str,
        keys: str,
        *,
        literal: bool = True,
        enter: bool = True,
    ) -> None: ...

    def capture_pane(
        self,
        target: str,
        lines: int = 100,
        *,
        strip_ansi_codes: bool = True,
    ) -> str: ...

    def kill_session(self, session: str) -> None: ...

    def attach_session(self, session: str) -> None: ...

    def list_sessions(self) -> list[str]: ...


def default_session_backend(
    *,
    executable: str | Path | None = None,
    stable: bool = True,
) -> SessionBackend:
    """Return the default local terminal backend without exposing its implementation.

    Application code should depend on this neutral factory plus the
    ``SessionBackend`` protocol. The current local implementation is tmux, but
    callers must not import ``doeff_agents.tmux`` directly; that keeps the
    terminal multiplexer replaceable by doeff-agents.
    """
    from .tmux import StableTmuxSessionBackend, TmuxSessionBackend

    resolved = _resolve_default_executable(executable)
    backend_cls = StableTmuxSessionBackend if stable else TmuxSessionBackend
    return backend_cls(executable=resolved)


def _resolve_default_executable(executable: str | Path | None) -> str | Path:
    if executable is not None:
        return executable
    resolved = shutil.which("tmux")
    if resolved is None:
        raise RuntimeError("a terminal session backend is required, but tmux was not found")
    return resolved


__all__ = [
    "SessionBackend",
    "SessionConfig",
    "SessionInfo",
    "default_session_backend",
]
