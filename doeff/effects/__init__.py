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
from .callstack import (
    ProgramCallFrame,
    ProgramCallFrameEffect,
    ProgramCallStack,
    ProgramCallStackEffect,
)
from .dep import Dep, DepInjectEffect, inject
from .future import Await, FutureAwaitEffect, FutureParallelEffect, Parallel, await_, parallel
from .gather import Gather, GatherDict, GatherDictEffect, GatherEffect, gather, gather_dict
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
    Catch,
    Fail,
    Finally,
    FirstSuccess,
    Recover,
    ResultCatchEffect,
    ResultFailEffect,
    ResultFinallyEffect,
    ResultFirstSuccessEffect,
    ResultRecoverEffect,
    ResultRetryEffect,
    ResultSafeEffect,
    ResultUnwrapEffect,
    Retry,
    Safe,
    Unwrap,
    catch,
    fail,
    finally_,
    first_success_effect,
    recover,
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
    "ProgramCallFrame",
    "ProgramCallFrameEffect",
    "ProgramCallStack",
    "ProgramCallStackEffect",
    "CaptureGraph",
    "Catch",
    "Dep",
    "DepInjectEffect",
    "Fail",
    "Finally",
    "FirstSuccess",
    "FutureAwaitEffect",
    "FutureParallelEffect",
    "Gather",
    "GatherDict",
    "GatherDictEffect",
    "GatherEffect",
    "Get",
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
    "Parallel",
    "Print",
    "Pure",
    # Effect classes
    "PureEffect",
    "Put",
    "Recover",
    "ResultCatchEffect",
    "ResultFailEffect",
    "ResultFinallyEffect",
    "ResultFirstSuccessEffect",
    "ResultRecoverEffect",
    "ResultRetryEffect",
    "ResultSafeEffect",
    "ResultUnwrapEffect",
    "Retry",
    "Safe",
    "Snapshot",
    "StateGetEffect",
    "StateModifyEffect",
    "StatePutEffect",
    "Step",
    "Thread",
    "ThreadEffect",
    "ThreadStrategy",
    "StructuredLog",
    "Tell",
    "Unwrap",
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
    "catch",
    "intercept_program_effect",
    "fail",
    "finally_",
    "first_success_effect",
    "gather",
    "gather_dict",
    "get",
    "graph",
    "inject",
    "io",
    "listen",
    "local",
    "log",
    "memo_get",
    "memo_put",
    "modify",
    "parallel",
    "perform",
    "print_",
    "put",
    "recover",
    "retry",
    "thread",
    "run",
    "safe",
    "slog",
    "snapshot",
    "step",
    "tell",
    "unwrap_result",
]
