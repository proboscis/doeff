"""Backward-compatible re-export for OpenCode handler imports."""

from .handlers.opencode import OpenCodeHandler, opencode_handler  # noqa: F401

__all__ = [
    "OpenCodeHandler",
    "opencode_handler",
]
