"""CESK machine effect handlers.

This module provides the Handler type and default handler registry for the
unified CESK architecture. Handlers transform effects into frame results,
which the CESK machine uses to continue execution.

Handler signature:
    (effect: EffectBase, task_state: TaskState, store: Store) -> FrameResult

The handler receives the effect, current task state, and shared store,
then returns a FrameResult indicating how to continue:
- ContinueValue: Continue with a value
- ContinueError: Continue with an error
- ContinueProgram: Continue with a new program to execute
- ContinueGenerator: Continue by resuming a generator
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, TypeAlias

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store
    from doeff.cesk.frames import FrameResult


Handler: TypeAlias = Callable[
    ["EffectBase", "TaskState", "Store"],
    "FrameResult"
]


def default_handlers() -> dict[type, Handler]:
    """Return default handler registry for all built-in effects.
    
    Returns a dict mapping effect types to their handler functions.
    Handlers are organized by category:
    - core: PureEffect, AskEffect, StateGetEffect, StatePutEffect, StateModifyEffect
    - control: LocalEffect, ResultSafeEffect, WriterListenEffect, InterceptEffect
    - task: GatherEffect, RaceEffect
    - io: IOPerformEffect, CacheGetEffect, CachePutEffect, CacheExistsEffect, CacheDeleteEffect
    - time: DelayEffect, GetTimeEffect
    """
    from doeff.effects.pure import PureEffect
    from doeff.effects.reader import AskEffect
    from doeff.effects.state import StateGetEffect, StatePutEffect, StateModifyEffect
    from doeff.effects.writer import WriterTellEffect
    from doeff.effects.io import IOPerformEffect
    from doeff.effects.cache import (
        CacheGetEffect,
        CachePutEffect,
        CacheExistsEffect,
        CacheDeleteEffect,
    )
    from doeff.effects.time import DelayEffect, GetTimeEffect
    
    from doeff.cesk.handlers.core import (
        handle_pure,
        handle_ask,
        handle_get,
        handle_put,
        handle_modify,
    )
    from doeff.cesk.handlers.control import (
        handle_tell,
    )
    from doeff.cesk.handlers.io import (
        handle_io,
        handle_cache_get,
        handle_cache_put,
        handle_cache_exists,
        handle_cache_delete,
    )
    from doeff.cesk.handlers.time import (
        handle_delay,
        handle_get_time,
    )
    
    return {
        PureEffect: handle_pure,
        AskEffect: handle_ask,
        StateGetEffect: handle_get,
        StatePutEffect: handle_put,
        StateModifyEffect: handle_modify,
        WriterTellEffect: handle_tell,
        IOPerformEffect: handle_io,
        CacheGetEffect: handle_cache_get,
        CachePutEffect: handle_cache_put,
        CacheExistsEffect: handle_cache_exists,
        CacheDeleteEffect: handle_cache_delete,
        DelayEffect: handle_delay,
        GetTimeEffect: handle_get_time,
    }


__all__ = [
    "Handler",
    "default_handlers",
]
