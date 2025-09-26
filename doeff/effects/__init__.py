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
from .io import IO, IOPerformEffect, IOPrintEffect, Print, perform, print_, run
from .memo import MemoGet, MemoGetEffect, MemoPut, MemoPutEffect, memo_get, memo_put
from .reader import Ask, AskEffect, Local, LocalEffect, ask, local
from .result import (
    Catch,
    Fail,
    Recover,
    ResultCatchEffect,
    ResultFailEffect,
    ResultSafeEffect,
    ResultRecoverEffect,
    ResultRetryEffect,
    ResultUnwrapEffect,
    ResultFirstSuccessEffect,
    Retry,
    Safe,
    Unwrap,
    FirstSuccess,
    catch,
    fail,
    unwrap_result,
    safe,
    recover,
    retry,
    first_success_effect,
    unwrap_result,
)
from .state import Get, Modify, Put, StateGetEffect, StateModifyEffect, StatePutEffect, get, modify, put
from .writer import (
    Listen,
    Log,
    Tell,
    WriterListenEffect,
    WriterTellEffect,
    listen,
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
    # Effect classes
    "AskEffect",
    "LocalEffect",
    "StateGetEffect",
    "StatePutEffect",
    "StateModifyEffect",
    "WriterTellEffect",
    "WriterListenEffect",
    "FutureAwaitEffect",
    "FutureParallelEffect",
    "ResultFailEffect",
    "ResultCatchEffect",
    "ResultSafeEffect",
    "ResultRecoverEffect",
    "ResultRetryEffect",
    "ResultUnwrapEffect",
    "ResultFirstSuccessEffect",
    "GraphStepEffect",
    "GraphAnnotateEffect",
    "GraphSnapshotEffect",
    "GraphCaptureEffect",
    "DepInjectEffect",
    "GatherEffect",
    "GatherDictEffect",
    "CacheGetEffect",
    "CachePutEffect",
    "MemoGetEffect",
    "MemoPutEffect",
    "IOPerformEffect",
    "IOPrintEffect",
    "AtomicGetEffect",
    "AtomicUpdateEffect",
    # Factory helpers
    "Ask",
    "Local",
    "Get",
    "Put",
    "Modify",
    "Tell",
    "Listen",
    "Log",
    "Await",
    "Parallel",
    "Fail",
    "Catch",
    "Safe",
    "Recover",
    "Retry",
    "Unwrap",
    "FirstSuccess",
    "Step",
    "Annotate",
    "Snapshot",
    "CaptureGraph",
    "Dep",
    "Gather",
    "GatherDict",
    "CacheGet",
    "CachePut",
    "MemoGet",
    "MemoPut",
    "IO",
    "Print",
    "perform",
    "run",
    "print_",
    "AtomicGet",
    "AtomicUpdate",
    # Compatibility aliases
    "ask",
    "local",
    "get",
    "put",
    "modify",
    "log",
    "tell",
    "listen",
    "await_",
    "parallel",
    "fail",
    "catch",
    "safe",
    "recover",
    "retry",
    "first_success_effect",
    "unwrap_result",
    "io",
    "step",
    "annotate",
    "snapshot",
    "capture_graph",
    "capture",
    "memo_get",
    "memo_put",
    "cache_get",
    "cache_put",
    "atomic_get",
    "atomic_update",
]
