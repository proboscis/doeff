"""Handlers for doeff-agentic effects."""

from __future__ import annotations

from typing import Any

from .opencode import OpenCodeHandler, opencode_handler
from .testing import MockAgenticHandler, MockAgenticState, mock_handlers
from .tmux import TmuxHandler, tmux_handler

try:
    from .production import (
        AgenticHandler,
        WorkflowContext,
        agent_handler,
        agentic_effectful_handlers,
        with_agentic_effectful_handlers,
    )
except ImportError:
    AgenticHandler = None  # type: ignore[assignment]
    WorkflowContext = None  # type: ignore[assignment]
    agent_handler = None  # type: ignore[assignment]
    agentic_effectful_handlers = None  # type: ignore[assignment]
    with_agentic_effectful_handlers = None  # type: ignore[assignment]


def production_handlers(
    *,
    backend: str = "opencode",
    server_url: str | None = None,
    hostname: str = "127.0.0.1",
    port: int | None = None,
    startup_timeout: float = 30.0,
    working_dir: str | None = None,
) -> dict[type, Any]:
    """Create production handler maps for new agentic effects."""
    if backend == "opencode":
        return opencode_handler(
            server_url=server_url,
            hostname=hostname,
            port=port,
            startup_timeout=startup_timeout,
            working_dir=working_dir,
        )
    if backend == "tmux":
        return tmux_handler(working_dir=working_dir)
    raise ValueError(f"Unsupported backend: {backend}")


__all__ = [
    "AgenticHandler",
    "MockAgenticHandler",
    "MockAgenticState",
    "OpenCodeHandler",
    "TmuxHandler",
    "WorkflowContext",
    "agent_handler",
    "agentic_effectful_handlers",
    "mock_handlers",
    "opencode_handler",
    "production_handlers",
    "tmux_handler",
    "with_agentic_effectful_handlers",
]
