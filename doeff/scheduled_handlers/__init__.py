"""Scheduled effect handlers for the doeff CESK interpreter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.runtime import ScheduledHandlers

from doeff.scheduled_handlers.state import (
    handle_state_get,
    handle_state_put,
    handle_state_modify,
)

from doeff.scheduled_handlers.reader import handle_ask

from doeff.scheduled_handlers.writer import handle_writer_tell

from doeff.scheduled_handlers.pure import handle_pure_effect

from doeff.scheduled_handlers.cache import (
    handle_cache_get,
    handle_cache_put,
    handle_cache_delete,
    handle_cache_exists,
)

from doeff.scheduled_handlers.io import handle_io_perform

from doeff.scheduled_handlers.concurrency import (
    handle_future_await,
    handle_spawn,
    handle_spawn_scheduled,
    handle_task_join,
    _get_shared_executor,
)

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
        SpawnEffect,
        StateGetEffect,
        StateModifyEffect,
        StatePutEffect,
        TaskJoinEffect,
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
        PureEffect: handle_pure_effect,
        CacheGetEffect: handle_cache_get,
        CachePutEffect: handle_cache_put,
        CacheDeleteEffect: handle_cache_delete,
        CacheExistsEffect: handle_cache_exists,
        IOPerformEffect: handle_io_perform,
        FutureAwaitEffect: handle_future_await,
        SpawnEffect: handle_spawn,
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
    "handle_pure_effect",
    "handle_cache_get",
    "handle_cache_put",
    "handle_cache_delete",
    "handle_cache_exists",
    "handle_io_perform",
    "handle_future_await",
    "handle_spawn",
    "handle_spawn_scheduled",
    "handle_task_join",
    "handle_delay",
    "handle_wait_until",
    "handle_get_time",
]
