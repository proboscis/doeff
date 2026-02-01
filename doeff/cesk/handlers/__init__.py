"""CESK machine effect handlers (v2 architecture).

This module exports the new handler-based effect system:
- Handler: Type alias for handler functions
- HandlerContext: Context passed to handlers (from handler_frame.py)
- Handlers for different runner types:
  - SyncRunner: sync_await_handler (handles Await via background thread)
  - AsyncRunner: python_async_handler (produces PythonAsyncSyntaxEscape)
- Common handlers: core_handler, task_scheduler_handler, scheduler_state_handler

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

# Import v2 handlers (new names)
from doeff.cesk.handlers.core_handler import core_handler
from doeff.cesk.handlers.python_async_handler import python_async_handler
from doeff.cesk.handlers.scheduler_state_handler import scheduler_state_handler
from doeff.cesk.handlers.sync_await_handler import sync_await_handler
from doeff.cesk.handlers.task_scheduler_handler import task_scheduler_handler
from doeff.cesk.handlers.threaded_asyncio_handler import threaded_asyncio_handler

# Backwards compatibility aliases (deprecated)
async_effects_handler = python_async_handler
queue_handler = scheduler_state_handler
scheduler_handler = task_scheduler_handler

__all__ = [
    "Handler",
    "HandlerContext",
    # New names (preferred)
    "core_handler",
    "python_async_handler",
    "scheduler_state_handler",
    "sync_await_handler",
    "task_scheduler_handler",
    "threaded_asyncio_handler",
    # Backwards compatibility aliases (deprecated)
    "async_effects_handler",
    "queue_handler",
    "scheduler_handler",
]
