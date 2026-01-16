"""Handler registry and types for the unified CESK machine.

This module provides the Handler type and default handler registry for all
built-in effects in the doeff system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, TypeAlias

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.frames import FrameResult
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store


Handler: TypeAlias = Callable[[Any, Any, Any], Any]


def default_handlers() -> dict[type, Handler]:
    """Return default handler registry for all built-in effects.
    
    Returns a mapping from effect type to handler function.
    Handlers are lazily imported to avoid circular dependencies.
    """
    from doeff.cesk.handlers.core import (
        handle_ask,
        handle_get,
        handle_modify,
        handle_pure,
        handle_put,
    )
    from doeff.cesk.handlers.control import (
        handle_intercept,
        handle_listen,
        handle_local,
        handle_safe,
    )
    from doeff.cesk.handlers.io import (
        handle_cache_delete,
        handle_cache_exists,
        handle_cache_get,
        handle_cache_put,
        handle_io,
    )
    from doeff.cesk.handlers.task import handle_gather, handle_race
    from doeff.cesk.handlers.time import handle_delay, handle_get_time
    from doeff.effects.cache import (
        CacheDeleteEffect,
        CacheExistsEffect,
        CacheGetEffect,
        CachePutEffect,
    )
    from doeff.effects.gather import GatherEffect
    from doeff.effects.intercept import InterceptEffect
    from doeff.effects.io import IOPerformEffect
    from doeff.effects.pure import PureEffect
    from doeff.effects.reader import AskEffect, LocalEffect
    from doeff.effects.result import ResultSafeEffect
    from doeff.effects.state import StateGetEffect, StateModifyEffect, StatePutEffect
    from doeff.effects.time import DelayEffect, GetTimeEffect
    from doeff.effects.writer import WriterListenEffect

    # Build handler registry
    registry: dict[type, Handler] = {
        # Core effects
        PureEffect: handle_pure,
        AskEffect: handle_ask,
        StateGetEffect: handle_get,
        StatePutEffect: handle_put,
        StateModifyEffect: handle_modify,
        # Control flow effects
        LocalEffect: handle_local,
        ResultSafeEffect: handle_safe,
        WriterListenEffect: handle_listen,
        InterceptEffect: handle_intercept,
        # Task effects
        GatherEffect: handle_gather,
        # IO effects
        IOPerformEffect: handle_io,
        CacheGetEffect: handle_cache_get,
        CachePutEffect: handle_cache_put,
        CacheExistsEffect: handle_cache_exists,
        CacheDeleteEffect: handle_cache_delete,
        # Time effects
        DelayEffect: handle_delay,
        GetTimeEffect: handle_get_time,
    }

    return registry


__all__ = [
    "Handler",
    "default_handlers",
]
