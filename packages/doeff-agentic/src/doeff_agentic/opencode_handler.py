"""Backward-compatible re-export for OpenCode handler imports."""

from .handlers.opencode import OpenCodeHandler, opencode_handler

__all__ = [
    "OpenCodeHandler",
    "opencode_handler",
]
