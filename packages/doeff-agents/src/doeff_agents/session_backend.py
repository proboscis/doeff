"""Backend-neutral terminal session transport primitives.

段 7 lane 7c(agora-redesign・決定 1.3): 既定の backend を組む時の PATH の
探索は `doeff_agents.io_effects` の要求で、実行する家(本番 / 検)は
``io_root`` として渡される — この module は composition root であって、
自分では実世界に触らない。
"""


from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import hy  # noqa: F401  # .hy import hook — the I/O effect vocabulary is a Hy module
from doeff import do

from doeff_agents.io_effects import which_executable
from doeff_agents.io_root import IoGenerator, IoRoot, as_optional_str


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

    def capture_transcript(
        self,
        target: str,
        lines: int = 100,
        *,
        strip_ansi_codes: bool = True,
    ) -> str: ...

    def kill_session(self, session: str) -> None: ...

    def attach_session(self, session: str) -> None: ...

    def list_sessions(self) -> list[str]: ...


@do
def resolve_default_executable(executable: str | Path | None = None) -> IoGenerator[str | Path]:
    """Program answering which terminal multiplexer binary the backend will drive."""
    if executable is not None:
        return executable
    resolved = as_optional_str((yield which_executable("tmux")))
    if resolved is None:
        raise RuntimeError("a terminal session backend is required, but tmux was not found")
    return resolved


def default_session_backend(
    *,
    executable: str | Path | None = None,
    stable: bool = True,
    io_root: IoRoot | None = None,
) -> SessionBackend:
    """Return the default local terminal backend without exposing its implementation.

    Application code should depend on this neutral factory plus the
    ``SessionBackend`` protocol. The current local implementation is tmux, but
    callers must not import ``doeff_agents.tmux`` directly; that keeps the
    terminal multiplexer replaceable by doeff-agents.

    ``io_root`` is the composition-root choice of I/O家: the production handler
    by default, the in-memory fake in tests.
    """
    from .io_handlers import run_driver_io
    from .tmux import StableTmuxSessionBackend, TmuxSessionBackend

    root: IoRoot = io_root if io_root is not None else run_driver_io
    resolved = root(resolve_default_executable(executable))
    if not isinstance(resolved, (str, Path)):
        raise RuntimeError(f"terminal session backend の実行ファイルが解けない: {resolved!r}")
    backend_cls = StableTmuxSessionBackend if stable else TmuxSessionBackend
    return backend_cls(executable=resolved, io_root=root)


__all__ = [
    "SessionBackend",
    "SessionConfig",
    "SessionInfo",
    "default_session_backend",
    "resolve_default_executable",
]
