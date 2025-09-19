"""Public effect API for doeff."""

from __future__ import annotations

from .cache import CacheGet, CacheGetEffect, CachePut, CachePutEffect, cache_get, cache_put
from .dep import Dep, DepInjectEffect, inject
from .future import Await, FutureAwaitEffect, FutureParallelEffect, Parallel, await_, parallel
from .gather import Gather, GatherDict, GatherDictEffect, GatherEffect, gather, gather_dict
from .graph import (
    Annotate,
    GraphAnnotateEffect,
    GraphSnapshotEffect,
    GraphStepEffect,
    Snapshot,
    Step,
)
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
    Retry,
    Safe,
    Unwrap,
    catch,
    fail,
    safe,
    recover,
    retry,
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
    "GraphStepEffect",
    "GraphAnnotateEffect",
    "GraphSnapshotEffect",
    "DepInjectEffect",
    "GatherEffect",
    "GatherDictEffect",
    "CacheGetEffect",
    "CachePutEffect",
    "MemoGetEffect",
    "MemoPutEffect",
    "IOPerformEffect",
    "IOPrintEffect",
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
    "Step",
    "Annotate",
    "Snapshot",
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
    "unwrap_result",
    "io",
    "step",
    "annotate",
    "snapshot",
    "memo_get",
    "memo_put",
    "cache_get",
    "cache_put",
]
