"""CESK machine effect handlers (v2 architecture).

This module exports the new handler-based effect system:
- Handler: Type alias for handler functions
- HandlerContext: Context passed to handlers (from handler_frame.py)
- The v2 handlers: core_handler, scheduler_handler, queue_handler, async_effects_handler

The old v1 handler functions (handle_ask, handle_get, etc.) and default_handlers()
have been removed. Use the v2 WithHandler-based system instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeAlias

from doeff.cesk.handler_frame import HandlerContext

if TYPE_CHECKING:
    from collections.abc import Callable

    from doeff.cesk.frames import FrameResult

# Handler type alias for v2 handler functions
# Handlers take (effect, context) and return a FrameResult
Handler: TypeAlias = "Callable[[Any, HandlerContext], FrameResult]"

# Import v2 handlers for convenience
from doeff.cesk.handlers.async_effects_handler import async_effects_handler
from doeff.cesk.handlers.core_handler import core_handler
from doeff.cesk.handlers.queue_handler import queue_handler
from doeff.cesk.handlers.scheduler_handler import scheduler_handler
from doeff.cesk.handlers.threaded_asyncio_handler import threaded_asyncio_handler

__all__ = [
    "Handler",
    "HandlerContext",
    "async_effects_handler",
    "core_handler",
    "queue_handler",
    "scheduler_handler",
    "threaded_asyncio_handler",
]
