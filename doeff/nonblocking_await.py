"""Backward compatibility helpers for nonblocking await.

Use doeff.effects.future.sync_await_handler directly in new code.
"""

from __future__ import annotations

from typing import Any

from doeff import WithHandler
from doeff.effects.future import sync_await_handler as nonblocking_await_handler


def get_loop():
    """Return the shared background event loop used by sync_await_handler."""
    from doeff.effects.future import _ensure_background_loop

    return _ensure_background_loop()


def with_nonblocking_await(program: Any) -> Any:
    """Wrap a program with the backward-compatible await handler alias."""
    return WithHandler(handler=nonblocking_await_handler, expr=program)


__all__ = ["get_loop", "nonblocking_await_handler", "with_nonblocking_await"]
