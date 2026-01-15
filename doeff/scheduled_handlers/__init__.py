"""Scheduled effect handlers for the doeff CESK interpreter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.runtime import ScheduledHandlers

from doeff.scheduled_handlers.state import (
    handle_state_get,
    handle_state_put,
    handle_state_modify,
)

from doeff.scheduled_handlers.reader import handle_ask, handle_local

from doeff.scheduled_handlers.writer import handle_writer_tell, handle_writer_listen

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
    handle_gather,
    _get_shared_executor,
)

from doeff.scheduled_handlers.time import (
    handle_delay,
    handle_wait_until,
    handle_get_time,
)

from doeff.scheduled_handlers.result import handle_safe

from doeff.scheduled_handlers.intercept import handle_intercept

from doeff.scheduled_handlers.callstack import handle_call_frame, handle_call_stack

from doeff.scheduled_handlers.graph import (
    handle_graph_step,
    handle_graph_annotate,
    handle_graph_snapshot,
    handle_graph_capture,
)

from doeff.scheduled_handlers.atomic import handle_atomic_get, handle_atomic_update


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
    from doeff.effects.reader import LocalEffect
    from doeff.effects.writer import WriterListenEffect
    from doeff.effects.gather import GatherEffect
    from doeff.effects.result import ResultSafeEffect
    from doeff.effects.intercept import InterceptEffect
    from doeff.effects.callstack import ProgramCallFrameEffect, ProgramCallStackEffect
    from doeff.effects.graph import (
        GraphStepEffect,
        GraphAnnotateEffect,
        GraphSnapshotEffect,
        GraphCaptureEffect,
    )
    from doeff.effects.atomic import AtomicGetEffect, AtomicUpdateEffect

    return {
        StateGetEffect: handle_state_get,
        StatePutEffect: handle_state_put,
        StateModifyEffect: handle_state_modify,
        AskEffect: handle_ask,
        LocalEffect: handle_local,
        WriterTellEffect: handle_writer_tell,
        WriterListenEffect: handle_writer_listen,
        PureEffect: handle_pure_effect,
        CacheGetEffect: handle_cache_get,
        CachePutEffect: handle_cache_put,
        CacheDeleteEffect: handle_cache_delete,
        CacheExistsEffect: handle_cache_exists,
        IOPerformEffect: handle_io_perform,
        FutureAwaitEffect: handle_future_await,
        SpawnEffect: handle_spawn,
        TaskJoinEffect: handle_task_join,
        GatherEffect: handle_gather,
        DelayEffect: handle_delay,
        WaitUntilEffect: handle_wait_until,
        GetTimeEffect: handle_get_time,
        ResultSafeEffect: handle_safe,
        InterceptEffect: handle_intercept,
        ProgramCallFrameEffect: handle_call_frame,
        ProgramCallStackEffect: handle_call_stack,
        GraphStepEffect: handle_graph_step,
        GraphAnnotateEffect: handle_graph_annotate,
        GraphSnapshotEffect: handle_graph_snapshot,
        GraphCaptureEffect: handle_graph_capture,
        AtomicGetEffect: handle_atomic_get,
        AtomicUpdateEffect: handle_atomic_update,
    }


__all__ = [
    "default_scheduled_handlers",
    "handle_state_get",
    "handle_state_put",
    "handle_state_modify",
    "handle_ask",
    "handle_local",
    "handle_writer_tell",
    "handle_writer_listen",
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
    "handle_gather",
    "handle_delay",
    "handle_wait_until",
    "handle_get_time",
    "handle_safe",
    "handle_intercept",
    "handle_call_frame",
    "handle_call_stack",
    "handle_graph_step",
    "handle_graph_annotate",
    "handle_graph_snapshot",
    "handle_graph_capture",
    "handle_atomic_get",
    "handle_atomic_update",
]
