"""CESK machine effect handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, TypeAlias

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store
    from doeff.cesk.frames import FrameResult

Handler: TypeAlias = Callable[..., Any]


def default_handlers() -> dict[type, Handler]:
    from doeff.effects.atomic import AtomicGetEffect, AtomicUpdateEffect
    from doeff.effects.cache import (
        CacheDeleteEffect,
        CacheExistsEffect,
        CacheGetEffect,
        CachePutEffect,
    )
    from doeff.effects.callstack import ProgramCallFrameEffect, ProgramCallStackEffect
    from doeff.effects.gather import GatherEffect
    from doeff.effects.graph import (
        GraphAnnotateEffect,
        GraphCaptureEffect,
        GraphSnapshotEffect,
        GraphStepEffect,
    )
    from doeff.effects.intercept import InterceptEffect
    from doeff.effects.io import IOPerformEffect
    from doeff.effects.pure import PureEffect
    from doeff.effects.reader import AskEffect, LocalEffect
    from doeff.effects.result import ResultSafeEffect
    from doeff.effects.state import StateGetEffect, StateModifyEffect, StatePutEffect
    from doeff.effects.time import DelayEffect, GetTimeEffect
    from doeff.effects.writer import WriterListenEffect, WriterTellEffect

    from doeff.cesk.handlers.atomic import handle_atomic_get, handle_atomic_update
    from doeff.cesk.handlers.callstack import (
        handle_program_call_frame,
        handle_program_call_stack,
    )
    from doeff.cesk.handlers.control import (
        handle_intercept,
        handle_listen,
        handle_local,
        handle_safe,
        handle_tell,
    )
    from doeff.cesk.handlers.core import (
        handle_ask,
        handle_pure,
        handle_state_get,
        handle_state_modify,
        handle_state_put,
    )
    from doeff.cesk.handlers.graph import (
        handle_graph_annotate,
        handle_graph_capture,
        handle_graph_snapshot,
        handle_graph_step,
    )
    from doeff.cesk.handlers.io import (
        handle_cache_delete,
        handle_cache_exists,
        handle_cache_get,
        handle_cache_put,
        handle_io,
    )
    from doeff.cesk.handlers.task import handle_gather
    from doeff.cesk.handlers.time import handle_delay, handle_get_time

    return {
        PureEffect: handle_pure,
        AskEffect: handle_ask,
        StateGetEffect: handle_state_get,
        StatePutEffect: handle_state_put,
        StateModifyEffect: handle_state_modify,
        LocalEffect: handle_local,
        ResultSafeEffect: handle_safe,
        WriterListenEffect: handle_listen,
        InterceptEffect: handle_intercept,
        WriterTellEffect: handle_tell,
        GatherEffect: handle_gather,
        IOPerformEffect: handle_io,
        CacheGetEffect: handle_cache_get,
        CachePutEffect: handle_cache_put,
        CacheExistsEffect: handle_cache_exists,
        CacheDeleteEffect: handle_cache_delete,
        DelayEffect: handle_delay,
        GetTimeEffect: handle_get_time,
        AtomicGetEffect: handle_atomic_get,
        AtomicUpdateEffect: handle_atomic_update,
        GraphStepEffect: handle_graph_step,
        GraphAnnotateEffect: handle_graph_annotate,
        GraphSnapshotEffect: handle_graph_snapshot,
        GraphCaptureEffect: handle_graph_capture,
        ProgramCallFrameEffect: handle_program_call_frame,
        ProgramCallStackEffect: handle_program_call_stack,
    }


__all__ = [
    "Handler",
    "default_handlers",
]
