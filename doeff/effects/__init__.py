"""Public effect API for doeff."""

from __future__ import annotations

from .atomic import (
    AtomicGet,
    AtomicGetEffect,
    AtomicUpdate,
    AtomicUpdateEffect,
    atomic_get,
    atomic_update,
)
from .cache import CacheGet, CacheGetEffect, CachePut, CachePutEffect, cache_get, cache_put
from .durable_cache import (
    DurableCacheDelete,
    DurableCacheExists,
    DurableCacheGet,
    DurableCachePut,
    cachedelete,
    cacheexists,
    cacheget,
    cacheput,
)
from .callstack import (
    ProgramCallFrame,
    ProgramCallFrameEffect,
    ProgramCallStack,
    ProgramCallStackEffect,
)
from .dep import Dep, DepInjectEffect, inject
from .future import Await, FutureAwaitEffect, await_
from .gather import Gather, GatherEffect, gather
from .graph import (
    Annotate,
    CaptureGraph,
    GraphAnnotateEffect,
    GraphCaptureEffect,
    GraphSnapshotEffect,
    GraphStepEffect,
    Snapshot,
    Step,
    graph,
)
from .graph import capture as capture_graph
from .intercept import InterceptEffect, intercept_program_effect
from .io import IO, IOPerformEffect, IOPrintEffect, Print, perform, print_, run
from .memo import MemoGet, MemoGetEffect, MemoPut, MemoPutEffect, memo_get, memo_put
from .pure import Pure, PureEffect
from .reader import Ask, AskEffect, Local, LocalEffect, ask, local
from .result import (
    Fail,
    Finally,
    FirstSuccess,
    ResultFailEffect,
    ResultFinallyEffect,
    ResultFirstSuccessEffect,
    ResultRetryEffect,
    ResultSafeEffect,
    ResultUnwrapEffect,
    Retry,
    Safe,
    Unwrap,
    fail,
    finally_,
    first_success_effect,
    retry,
    safe,
    unwrap_result,
)
from .state import (
    Get,
    Modify,
    Put,
    StateGetEffect,
    StateModifyEffect,
    StatePutEffect,
    get,
    modify,
    put,
)
from .spawn import (
    Spawn,
    SpawnBackend,
    SpawnEffect,
    Task,
    TaskJoinEffect,
    spawn,
)
from .time import (
    Delay,
    DelayEffect,
    GetTime,
    GetTimeEffect,
    WaitUntil,
    WaitUntilEffect,
    delay,
    get_time,
    wait_until,
)
from .thread import Thread, ThreadEffect, ThreadStrategy, thread
from .writer import (
    Listen,
    Log,
    StructuredLog,
    Tell,
    WriterListenEffect,
    WriterTellEffect,
    listen,
    slog,
    tell,
)

# Lowercase compatibility aliases
# Functions imported above already provide lowercase helpers

# Legacy lowercase synonyms for backward compatibility
io = IO
step = Step
annotate = Annotate
snapshot = Snapshot
capture = capture_graph


__all__ = [
    "IO",
    "Annotate",
    # Factory helpers
    "Ask",
    "AskEffect",
    "AtomicGet",
    "AtomicGetEffect",
    "AtomicUpdate",
    "AtomicUpdateEffect",
    "Await",
    "CacheGet",
    "CacheGetEffect",
    "CachePut",
    "CachePutEffect",
    # Durable cache effects
    "DurableCacheDelete",
    "DurableCacheExists",
    "DurableCacheGet",
    "DurableCachePut",
    "cachedelete",
    "cacheexists",
    "cacheget",
    "cacheput",
    "ProgramCallFrame",
    "ProgramCallFrameEffect",
    "ProgramCallStack",
    "ProgramCallStackEffect",
    "CaptureGraph",
    "Delay",
    "DelayEffect",
    "Dep",
    "DepInjectEffect",
    "Fail",
    "Finally",
    "FirstSuccess",
    "FutureAwaitEffect",
    "Gather",
    "GatherEffect",
    "Get",
    "GetTime",
    "GetTimeEffect",
    "GraphAnnotateEffect",
    "GraphCaptureEffect",
    "GraphSnapshotEffect",
    "GraphStepEffect",
    "InterceptEffect",
    "IOPerformEffect",
    "IOPrintEffect",
    "Listen",
    "Local",
    "LocalEffect",
    "Log",
    "MemoGet",
    "MemoGetEffect",
    "MemoPut",
    "MemoPutEffect",
    "Modify",
    "Print",
    "Pure",
    # Effect classes
    "PureEffect",
    "Put",
    "ResultFailEffect",
    "ResultFinallyEffect",
    "ResultFirstSuccessEffect",
    "ResultRetryEffect",
    "ResultSafeEffect",
    "ResultUnwrapEffect",
    "Retry",
    "Safe",
    "Snapshot",
    "StateGetEffect",
    "StateModifyEffect",
    "StatePutEffect",
    "Spawn",
    "SpawnBackend",
    "SpawnEffect",
    "Task",
    "TaskJoinEffect",
    "Step",
    "Thread",
    "ThreadEffect",
    "ThreadStrategy",
    "StructuredLog",
    "Tell",
    "Unwrap",
    "WaitUntil",
    "WaitUntilEffect",
    "WriterListenEffect",
    "WriterTellEffect",
    "annotate",
    # Compatibility aliases
    "ask",
    "atomic_get",
    "atomic_update",
    "await_",
    "cache_get",
    "cache_put",
    "capture",
    "capture_graph",
    "delay",
    "intercept_program_effect",
    "fail",
    "finally_",
    "first_success_effect",
    "gather",
    "get",
    "get_time",
    "graph",
    "inject",
    "io",
    "listen",
    "local",
    "log",
    "memo_get",
    "memo_put",
    "modify",
    "perform",
    "print_",
    "put",
    "retry",
    "thread",
    "run",
    "safe",
    "slog",
    "snapshot",
    "spawn",
    "step",
    "tell",
    "unwrap_result",
    "wait_until",
]
