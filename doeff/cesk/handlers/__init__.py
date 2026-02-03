"""CESK machine effect handlers (v2 architecture).

This module exports the new handler-based effect system:
- Handler: Type alias for handler functions
- HandlerContext: Context passed to handlers (from handler_frame.py)
- Handlers for different runner types:
  - sync_run: sync_await_handler (handles Await via background thread)
  - async_run: python_async_syntax_escape_handler (produces PythonAsyncSyntaxEscape)
- Common handlers: core_handler, task_scheduler_handler, scheduler_state_handler

The old v1 handler functions (handle_ask, handle_get, etc.) and default_handlers()
have been removed. Use the v2 WithHandler-based system instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeAlias

from doeff.cesk.handler_frame import HandlerContext

if TYPE_CHECKING:
    from collections.abc import Callable

# Handler type alias for v2 handler functions
# Handlers take (effect, context) and return a FrameResult
Handler: TypeAlias = "Callable[[Any, HandlerContext], Any]"

# Import v2 handlers (new names)
from doeff.cesk.handlers.async_external_wait_handler import async_external_wait_handler
from doeff.cesk.handlers.atomic_handler import atomic_handler
from doeff.cesk.handlers.cache_handler import cache_handler
from doeff.cesk.handlers.core_handler import core_handler
from doeff.cesk.handlers.graph_handler import graph_handler
from doeff.cesk.handlers.python_async_syntax_escape_handler import python_async_syntax_escape_handler
from doeff.cesk.handlers.reader_handler import CircularAskError
from doeff.cesk.handlers.scheduler_state_handler import scheduler_state_handler
from doeff.cesk.handlers.state_handler import state_handler
from doeff.cesk.handlers.sync_await_handler import sync_await_handler
from doeff.cesk.handlers.sync_external_wait_handler import sync_external_wait_handler
from doeff.cesk.handlers.task_scheduler_handler import task_scheduler_handler
from doeff.cesk.handlers.writer_handler import writer_handler

# Backwards compatibility aliases (deprecated)
python_async_handler = python_async_syntax_escape_handler
async_effects_handler = python_async_syntax_escape_handler
queue_handler = scheduler_state_handler
scheduler_handler = task_scheduler_handler

__all__ = [
    "Handler",
    "HandlerContext",
    "CircularAskError",
    # New names (preferred)
    "async_external_wait_handler",
    "atomic_handler",
    "cache_handler",
    "core_handler",
    "graph_handler",
    "python_async_syntax_escape_handler",
    "scheduler_state_handler",
    "state_handler",
    "sync_await_handler",
    "sync_external_wait_handler",
    "task_scheduler_handler",
    "writer_handler",
    # Backwards compatibility aliases (deprecated)
    "async_effects_handler",
    "python_async_handler",
    "queue_handler",
    "scheduler_handler",
]
