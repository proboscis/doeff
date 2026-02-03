"""Sync handler for WaitForExternalCompletion - blocking queue.get().

This handler is used in sync_run's handler preset. When the scheduler yields
WaitForExternalCompletion (no runnable tasks, external promises pending),
this handler blocks the thread on queue.get() until a completion arrives.

This works for sync_run because external I/O runs in background threads
(via sync_await_handler), which can complete and call queue.put() while
the main thread is blocked.

See SPEC-CESK-004-handler-owned-blocking.md for architecture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff._types_internal import EffectBase
from doeff.do import do
from doeff.effects.scheduler_internal import WaitForExternalCompletion

if TYPE_CHECKING:
    from doeff.cesk.handler_frame import HandlerContext


@do
def sync_external_wait_handler(effect: EffectBase, ctx: "HandlerContext"):
    """Handle WaitForExternalCompletion by blocking on queue.get().

    This is the sync version - it blocks the thread directly.
    For async_run, use async_external_wait_handler instead.
    """
    if isinstance(effect, WaitForExternalCompletion):
        # Direct blocking - sync context can block thread
        # Background threads (from sync_await_handler) will complete
        # and call queue.put(), unblocking this get()
        item = effect.queue.get()
        return item

    # Forward other effects to outer handlers
    result = yield effect
    return result


__all__ = [
    "sync_external_wait_handler",
]
