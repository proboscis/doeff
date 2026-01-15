"""
doeff - Do-notation and Effects system for Python.

A practical free monad implementation that prioritizes usability and Python idioms
over theoretical purity. Uses generators for do-notation and supports comprehensive
effects including Reader, State, Writer, Future, Result, and IO.

Example:
    >>> from doeff import do, Program, Put, Get, Log
    >>>
    >>> @do
    >>> def example_program():
    ...     yield Put("counter", 0)
    ...     yield Log("Starting computation")
    ...     count = yield Get("counter")
    ...     return count + 1
"""

import os as _os
import sys as _sys

_PKG_DIR = _os.path.dirname(__file__)
if _PKG_DIR in _sys.path:
    _sys.path = [path for path in _sys.path if path != _PKG_DIR]

from doeff.types import (
    ExecutionContext,
    RunResult,
    Effect,
    EffectGenerator,
    ListenResult,
    DEFAULT_REPR_LIMIT,
    REPR_LIMIT_KEY,
    Ok,
    Err,
    Result,
    Maybe,
    Nothing,
    NOTHING,
    Some,
    TraceError,
    trace_err,
    WNode,
    WStep,
    WGraph,
    FrozenDict,
)
from doeff.analysis import EffectCallTree

from doeff.effects import (
    Ask,
    Local,
    Get,
    Put,
    Modify,
    AtomicGet,
    AtomicUpdate,
    Log,
    StructuredLog,
    Tell,
    Listen,
    Await,
    Spawn,
    Task,
    Delay,
    DelayEffect,
    WaitUntil,
    WaitUntilEffect,
    Safe,
    IO,
    Step,
    Annotate,
    Snapshot,
    CaptureGraph,
    Gather,
    CacheGet,
    CachePut,
    ask,
    local,
    get,
    put,
    modify,
    tell,
    listen,
    slog,
    await_,
    spawn,
    delay,
    wait_until,
    safe,
    io,
    step,
    annotate,
    snapshot,
    capture_graph,
    cache_get,
    cache_put,
    atomic_get,
    atomic_update,
)

from doeff.interpreter import ProgramInterpreter
from doeff.kleisli import KleisliProgram
from doeff.do import do
from doeff.program import Program
from doeff.graph_snapshot import (
    build_graph_snapshot,
    graph_to_html,
    graph_to_html_async,
    write_graph_html,
    write_graph_html_async,
)

from doeff.cache import (
    CACHE_PATH_ENV_KEY,
    cache,
    cache_key,
    cache_1min,
    cache_5min,
    cache_1hour,
    cache_forever,
    clear_persistent_cache,
    persistent_cache_path,
)
from doeff.cache_policy import CacheLifecycle, CachePolicy, CacheStorage

from doeff.run import run_program, ProgramRunResult

from doeff.runtime import (
    Resume,
    Schedule,
    HandlerResult,
    AwaitPayload,
    DelayPayload,
    WaitUntilPayload,
    SpawnPayload,
    SchedulePayload,
    Continuation,
    ScheduledEffectHandler,
    ScheduledHandlers,
)

from doeff.runtimes import (
    AsyncioRuntime,
    SyncRuntime,
    SimulationRuntime,
)

__version__ = "0.1.7"

capture = capture_graph

__all__ = [
    "Effect",
    "EffectGenerator",
    "Program",
    "ExecutionContext",
    "RunResult",
    "ListenResult",
    "CacheLifecycle",
    "CachePolicy",
    "CacheStorage",
    "DEFAULT_REPR_LIMIT",
    "REPR_LIMIT_KEY",
    "Ok",
    "Err",
    "Result",
    "Maybe",
    "Nothing",
    "NOTHING",
    "Some",
    "TraceError",
    "trace_err",
    "WNode",
    "WStep",
    "WGraph",
    "FrozenDict",
    "ProgramInterpreter",
    "KleisliProgram",
    "EffectCallTree",
    "do",
    "build_graph_snapshot",
    "graph_to_html",
    "graph_to_html_async",
    "write_graph_html",
    "write_graph_html_async",
    "IO",
    "Annotate",
    "Ask",
    "Await",
    "Delay",
    "DelayEffect",
    "WaitUntil",
    "WaitUntilEffect",
    "Safe",
    "Gather",
    "CacheGet",
    "CachePut",
    "CacheLifecycle",
    "CachePolicy",
    "CacheStorage",
    "Get",
    "Listen",
    "Local",
    "Log",
    "StructuredLog",
    "Modify",
    "AtomicGet",
    "AtomicUpdate",
    "Spawn",
    "Task",
    "Put",
    "Step",
    "CaptureGraph",
    "Tell",
    "annotate",
    "ask",
    "await_",
    "safe",
    "get",
    "io",
    "listen",
    "local",
    "modify",
    "atomic_get",
    "atomic_update",
    "spawn",
    "delay",
    "wait_until",
    "put",
    "step",
    "capture",
    "capture_graph",
    "tell",
    "slog",
    "cache_get",
    "cache_put",
    "CACHE_PATH_ENV_KEY",
    "cache",
    "cache_key",
    "cache_1min",
    "cache_5min",
    "cache_1hour",
    "cache_forever",
    "clear_persistent_cache",
    "persistent_cache_path",
    "run_program",
    "ProgramRunResult",
    "Resume",
    "Schedule",
    "HandlerResult",
    "AwaitPayload",
    "DelayPayload",
    "WaitUntilPayload",
    "SpawnPayload",
    "SchedulePayload",
    "Continuation",
    "ScheduledEffectHandler",
    "ScheduledHandlers",
    "AsyncioRuntime",
    "SyncRuntime",
    "SimulationRuntime",
]
