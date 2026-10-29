"""Handlers for doeff-agentic effects."""


from doeff.program import ProgramHandler

from .opencode import opencode_handler
from .recorded import recorded_workflows_handler
from .testing import mock_handlers
from .tmux import tmux_handler

try:
    from .production import agentic_effectful_handlers
except ImportError:
    agentic_effectful_handlers = None  # type: ignore[assignment]


def production_handlers(
    *,
    backend: str = "opencode",
    server_url: str | None = None,
    hostname: str = "127.0.0.1",
    port: int | None = None,
    startup_timeout: float = 30.0,
    working_dir: str | None = None,
) -> ProgramHandler:
    """Create a production protocol handler for new agentic effects."""
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
    "agentic_effectful_handlers",
    "mock_handlers",
    "opencode_handler",
    "production_handlers",
    "recorded_workflows_handler",
    "tmux_handler",
]
