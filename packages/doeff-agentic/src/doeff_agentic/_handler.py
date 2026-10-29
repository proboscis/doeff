"""Backward-compatible re-export for legacy handler imports."""

from ._handlers.production import (
    agentic_effectful_handlers,
    with_agentic_effectful_handlers,
)

__all__ = [
    "agentic_effectful_handlers",
    "with_agentic_effectful_handlers",
]
