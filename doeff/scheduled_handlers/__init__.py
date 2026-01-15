"""Scheduled effect handlers for the doeff CESK interpreter.

This module provides ScheduledEffectHandler implementations for all built-in effects.
Each handler is a pure function with the signature:

    def handler(effect: EffectBase, env: Environment, store: Store) -> HandlerResult

Handlers return one of:
- Resume(value, store): Resume immediately with value
- Schedule(payload, store): Defer to scheduler with explicit payload type
  - AwaitPayload(awaitable): Await async operation
  - DelayPayload(duration): Wait for duration
  - WaitUntilPayload(target): Wait until datetime
  - SpawnPayload(program, env, store): Spawn child program

The runtime takes care of passing payloads to the scheduler and managing continuations.

Module Organization:
- state.py: handle_state_get, handle_state_put, handle_state_modify
- reader.py: handle_ask
- writer.py: handle_writer_tell
- memo.py: handle_memo_get, handle_memo_put
- pure.py: handle_pure_effect
- cache.py: handle_cache_get, handle_cache_put,
            handle_cache_delete, handle_cache_exists
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
    handle_cache_get,
    handle_cache_put,
    handle_cache_delete,
    handle_cache_exists,
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
    handle_spawn_scheduled,
    handle_thread,
    handle_task_join,
    _get_shared_executor,
)

# Time handlers
from doeff.scheduled_handlers.time import (
    handle_delay,
    handle_wait_until,
    handle_get_time,
)


def default_scheduled_handlers() -> ScheduledHandlers:
    from doeff.effects import (
        AskEffect,
        DelayEffect,
        FutureAwaitEffect,
        GetTimeEffect,
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
        WaitUntilEffect,
        WriterTellEffect,
    )
    from doeff.effects.cache import (
        CacheDeleteEffect,
        CacheExistsEffect,
        CacheGetEffect,
        CachePutEffect,
    )
    from doeff.effects.pure import PureEffect

    return {
        StateGetEffect: handle_state_get,
        StatePutEffect: handle_state_put,
        StateModifyEffect: handle_state_modify,
        AskEffect: handle_ask,
        WriterTellEffect: handle_writer_tell,
        MemoGetEffect: handle_memo_get,
        MemoPutEffect: handle_memo_put,
        PureEffect: handle_pure_effect,
        CacheGetEffect: handle_cache_get,
        CachePutEffect: handle_cache_put,
        CacheDeleteEffect: handle_cache_delete,
        CacheExistsEffect: handle_cache_exists,
        IOPerformEffect: handle_io_perform,
        IOPrintEffect: handle_io_print,
        FutureAwaitEffect: handle_future_await,
        SpawnEffect: handle_spawn,
        ThreadEffect: handle_thread,
        TaskJoinEffect: handle_task_join,
        DelayEffect: handle_delay,
        WaitUntilEffect: handle_wait_until,
        GetTimeEffect: handle_get_time,
    }


__all__ = [
    "default_scheduled_handlers",
    "handle_state_get",
    "handle_state_put",
    "handle_state_modify",
    "handle_ask",
    "handle_writer_tell",
    "handle_memo_get",
    "handle_memo_put",
    "handle_pure_effect",
    "handle_cache_get",
    "handle_cache_put",
    "handle_cache_delete",
    "handle_cache_exists",
    "handle_io_perform",
    "handle_io_print",
    "handle_future_await",
    "handle_spawn",
    "handle_spawn_scheduled",
    "handle_thread",
    "handle_task_join",
    "handle_delay",
    "handle_wait_until",
    "handle_get_time",
]
