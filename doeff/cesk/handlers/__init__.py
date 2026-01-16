"""Handler infrastructure for the unified CESK architecture."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, TypeAlias

from doeff._types_internal import EffectBase
from doeff.cesk.actions import Action

if TYPE_CHECKING:
    from doeff.cesk.step import HandlerContext

Handler: TypeAlias = Callable[[Any, "HandlerContext"], tuple[Action, ...]]
HandlerRegistry: TypeAlias = dict[type, Handler]


def default_handlers() -> HandlerRegistry:
    from doeff.cesk.handlers.control import (
        handle_gather,
        handle_intercept,
        handle_local,
        handle_safe,
    )
    from doeff.cesk.handlers.core import (
        handle_ask,
        handle_get,
        handle_modify,
        handle_put,
        handle_tell,
    )
    from doeff.cesk.handlers.task import (
        handle_io,
        handle_spawn,
        handle_task_join,
    )
    from doeff.cesk.handlers.time import (
        handle_delay,
        handle_get_time,
        handle_wait_until,
    )
    from doeff.effects import (
        AskEffect,
        DelayEffect,
        GatherEffect,
        GetTimeEffect,
        InterceptEffect,
        IOPerformEffect,
        LocalEffect,
        ResultSafeEffect,
        SpawnEffect,
        StateGetEffect,
        StateModifyEffect,
        StatePutEffect,
        TaskJoinEffect,
        WaitUntilEffect,
        WriterTellEffect,
    )
    
    return {
        AskEffect: handle_ask,
        DelayEffect: handle_delay,
        GatherEffect: handle_gather,
        GetTimeEffect: handle_get_time,
        InterceptEffect: handle_intercept,
        IOPerformEffect: handle_io,
        LocalEffect: handle_local,
        ResultSafeEffect: handle_safe,
        SpawnEffect: handle_spawn,
        StateGetEffect: handle_get,
        StateModifyEffect: handle_modify,
        StatePutEffect: handle_put,
        TaskJoinEffect: handle_task_join,
        WaitUntilEffect: handle_wait_until,
        WriterTellEffect: handle_tell,
    }


__all__ = [
    "Handler",
    "HandlerRegistry",
    "default_handlers",
]
