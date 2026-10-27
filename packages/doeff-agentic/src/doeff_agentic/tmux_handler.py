"""Backward-compatible re-export for tmux handler imports."""

from .handlers.tmux import tmux_handler

__all__ = [
    "tmux_handler",
]
