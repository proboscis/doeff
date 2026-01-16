"""Effect handlers for the unified CESK machine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from doeff._types_internal import EffectBase
from doeff.cesk.frames import Kontinuation
from doeff.cesk.types import Environment, Store
from doeff.cesk.handlers.core import (
    handle_ask,
    handle_get,
    handle_modify,
    handle_pure,
    handle_put,
    handle_tell,
)
from doeff.cesk.handlers.control import (
    handle_intercept,
    handle_listen,
    handle_local,
    handle_safe,
)
from doeff.cesk.handlers.time import (
    handle_delay,
    handle_get_time,
    handle_wait_until,
)
from doeff.cesk.handlers.task import (
    handle_gather,
    handle_join,
    handle_race,
    handle_task,
)
from doeff.cesk.handlers.io import (
    handle_await,
    handle_cache_delete,
    handle_cache_exists,
    handle_cache_get,
    handle_cache_put,
    handle_io,
)

from doeff.cesk.state import TaskState

Handler = Callable[[EffectBase, Kontinuation, Environment, Store], TaskState]


def default_handlers() -> dict[type, Handler]:
    from doeff.effects import (
        AskEffect,
        CacheDeleteEffect,
        CacheExistsEffect,
        CacheGetEffect,
        CachePutEffect,
        DelayEffect,
        FutureAwaitEffect,
        GatherEffect,
        GetTimeEffect,
        InterceptEffect,
        IOPerformEffect,
        PureEffect,
        ResultSafeEffect,
        StateGetEffect,
        StateModifyEffect,
        StatePutEffect,
        TaskJoinEffect,
        WaitUntilEffect,
        WriterListenEffect,
        WriterTellEffect,
        LocalEffect,
        SpawnEffect,
    )

    return {
        PureEffect: handle_pure,
        AskEffect: handle_ask,
        StateGetEffect: handle_get,
        StatePutEffect: handle_put,
        StateModifyEffect: handle_modify,
        WriterTellEffect: handle_tell,
        LocalEffect: handle_local,
        ResultSafeEffect: handle_safe,
        WriterListenEffect: handle_listen,
        InterceptEffect: handle_intercept,
        DelayEffect: handle_delay,
        WaitUntilEffect: handle_wait_until,
        GetTimeEffect: handle_get_time,
        GatherEffect: handle_gather,
        TaskJoinEffect: handle_join,
        SpawnEffect: handle_task,
        IOPerformEffect: handle_io,
        FutureAwaitEffect: handle_await,
        CacheGetEffect: handle_cache_get,
        CachePutEffect: handle_cache_put,
        CacheExistsEffect: handle_cache_exists,
        CacheDeleteEffect: handle_cache_delete,
    }


__all__ = [
    "Handler",
    "default_handlers",
    "handle_ask",
    "handle_await",
    "handle_cache_delete",
    "handle_cache_exists",
    "handle_cache_get",
    "handle_cache_put",
    "handle_delay",
    "handle_gather",
    "handle_get",
    "handle_get_time",
    "handle_intercept",
    "handle_io",
    "handle_join",
    "handle_listen",
    "handle_local",
    "handle_modify",
    "handle_pure",
    "handle_put",
    "handle_race",
    "handle_safe",
    "handle_task",
    "handle_tell",
    "handle_wait_until",
]
