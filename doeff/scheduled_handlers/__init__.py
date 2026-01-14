"""Scheduled effect handlers for the doeff CESK interpreter.

This module provides direct ScheduledEffectHandler implementations
for all built-in effects. Each handler has the unified signature:

    def handler(
        effect: EffectBase,
        env: Environment,
        store: Store,
        k: Continuation,
        scheduler: Scheduler | None,
    ) -> HandlerResult

This replaces the legacy wrapper approach where old handlers were
wrapped via _make_pure_scheduled_handler() and _make_async_scheduled_handler().
Direct implementations give handlers access to k (continuation) and
scheduler for advanced patterns like simulation time-based delays.

Module Organization:
- state.py: handle_state_get, handle_state_put, handle_state_modify
- reader.py: handle_ask
- writer.py: handle_writer_tell
- memo.py: handle_memo_get, handle_memo_put
- pure.py: handle_pure_effect
- cache.py: handle_durable_cache_get, handle_durable_cache_put,
            handle_durable_cache_delete, handle_durable_cache_exists
- io.py: handle_io_perform, handle_io_print
- concurrency.py: handle_future_await, handle_spawn, handle_thread, handle_task_join
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.runtime import ScheduledHandlers

# State handlers
from doeff.scheduled_handlers.state import (
    handle_state_get,
    handle_state_put,
    handle_state_modify,
)

# Reader handlers
from doeff.scheduled_handlers.reader import handle_ask

# Writer handlers
from doeff.scheduled_handlers.writer import handle_writer_tell

# Memo handlers
from doeff.scheduled_handlers.memo import (
    handle_memo_get,
    handle_memo_put,
)

# Pure handlers
from doeff.scheduled_handlers.pure import handle_pure_effect

# Cache handlers
from doeff.scheduled_handlers.cache import (
    handle_durable_cache_get,
    handle_durable_cache_put,
    handle_durable_cache_delete,
    handle_durable_cache_exists,
)

# IO handlers
from doeff.scheduled_handlers.io import (
    handle_io_perform,
    handle_io_print,
)

# Concurrency handlers
from doeff.scheduled_handlers.concurrency import (
    handle_future_await,
    handle_spawn,
    handle_thread,
    handle_task_join,
    _get_shared_executor,
)


def default_scheduled_handlers() -> ScheduledHandlers:
    """Return the default handlers for all built-in effects.
    
    Returns a mapping from effect type to handler function.
    Each handler is a direct ScheduledEffectHandler implementation
    with full access to continuation (k) and scheduler.
    """
    from doeff.effects import (
        AskEffect,
        FutureAwaitEffect,
        IOPerformEffect,
        IOPrintEffect,
        MemoGetEffect,
        MemoPutEffect,
        SpawnEffect,
        StateGetEffect,
        StateModifyEffect,
        StatePutEffect,
        TaskJoinEffect,
        ThreadEffect,
        WriterTellEffect,
    )
    from doeff.effects.durable_cache import (
        DurableCacheDelete,
        DurableCacheExists,
        DurableCacheGet,
        DurableCachePut,
    )
    from doeff.effects.pure import PureEffect

    return {
        # State effects
        StateGetEffect: handle_state_get,
        StatePutEffect: handle_state_put,
        StateModifyEffect: handle_state_modify,
        # Reader effects
        AskEffect: handle_ask,
        # Writer effects
        WriterTellEffect: handle_writer_tell,
        # Memo effects
        MemoGetEffect: handle_memo_get,
        MemoPutEffect: handle_memo_put,
        # Pure effects
        PureEffect: handle_pure_effect,
        # Durable cache effects
        DurableCacheGet: handle_durable_cache_get,
        DurableCachePut: handle_durable_cache_put,
        DurableCacheDelete: handle_durable_cache_delete,
        DurableCacheExists: handle_durable_cache_exists,
        # IO effects
        IOPerformEffect: handle_io_perform,
        IOPrintEffect: handle_io_print,
        # Concurrency effects
        FutureAwaitEffect: handle_future_await,
        SpawnEffect: handle_spawn,
        ThreadEffect: handle_thread,
        TaskJoinEffect: handle_task_join,
    }


__all__ = [
    # Main export
    "default_scheduled_handlers",
    # State handlers
    "handle_state_get",
    "handle_state_put",
    "handle_state_modify",
    # Reader handlers
    "handle_ask",
    # Writer handlers
    "handle_writer_tell",
    # Memo handlers
    "handle_memo_get",
    "handle_memo_put",
    # Pure handlers
    "handle_pure_effect",
    # Cache handlers
    "handle_durable_cache_get",
    "handle_durable_cache_put",
    "handle_durable_cache_delete",
    "handle_durable_cache_exists",
    # IO handlers
    "handle_io_perform",
    "handle_io_print",
    # Concurrency handlers
    "handle_future_await",
    "handle_spawn",
    "handle_thread",
    "handle_task_join",
]
