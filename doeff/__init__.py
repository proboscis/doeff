"""
doeff - Do-notation and Effects system for Python.

A practical free monad implementation that prioritizes usability and Python idioms
over theoretical purity. Uses generators for do-notation and supports comprehensive
effects including Reader, State, Writer, Future, Result, and IO.

Example:
    >>> from doeff import do, Program, Put, Get, Tell
    >>>
    >>> @do
    >>> def example_program():
    ...     yield Put("counter", 0)
    ...     yield Tell("Starting computation")
    ...     count = yield Get("counter")
    ...     return count + 1
"""

import os as _os
import sys as _sys

_PKG_DIR = _os.path.dirname(__file__)
if _PKG_DIR in _sys.path:
    _sys.path = [path for path in _sys.path if path != _PKG_DIR]

from doeff.analysis import EffectCallTree
from doeff.cache import (
    CACHE_PATH_ENV_KEY,
    cache,
    cache_1hour,
    cache_1min,
    cache_5min,
    cache_forever,
    cache_key,
    clear_persistent_cache,
    persistent_cache_path,
)
from doeff.cache_policy import CacheLifecycle, CachePolicy, CacheStorage
from doeff.cesk.errors import MissingEnvKeyError
from doeff.cesk.runtime import (
    AsyncRuntime,
    SimulationRuntime,
    SyncRuntime,
)
from doeff.do import do
from doeff.effects import (
    IO,
    Annotate,
    Ask,
    AtomicGet,
    AtomicUpdate,
    Await,
    # Effects - Cache
    CacheDelete,
    CacheExists,
    CacheGet,
    CachePut,
    CaptureGraph,
    Delay,
    DelayEffect,
    Gather,
    Get,
    Listen,
    Local,
    Modify,
    Put,
    Safe,
    Snapshot,
    Spawn,
    Step,
    StructuredLog,
    Task,
    Tell,
    WaitUntil,
    WaitUntilEffect,
    annotate,
    ask,
    atomic_get,
    atomic_update,
    await_,
    cache_delete,
    cache_exists,
    cache_get,
    cache_put,
    capture_graph,
    delay,
    get,
    io,
    listen,
    local,
    modify,
    put,
    safe,
    slog,
    snapshot,
    spawn,
    step,
    tell,
    wait_until,
)
from doeff.graph_snapshot import (
    build_graph_snapshot,
    graph_to_html,
    graph_to_html_async,
    write_graph_html,
    write_graph_html_async,
)
from doeff.kleisli import KleisliProgram
from doeff.program import Program
from doeff.run import ProgramRunResult, run_program
from doeff.types import (
    DEFAULT_REPR_LIMIT,
    NOTHING,
    REPR_LIMIT_KEY,
    Effect,
    EffectGenerator,
    Err,
    ExecutionContext,
    FrozenDict,
    ListenResult,
    Maybe,
    Nothing,
    Ok,
    Result,
    RunResult,
    Some,
    TraceError,
    WGraph,
    WNode,
    WStep,
    trace_err,
)

__version__ = "0.1.7"

capture = capture_graph

__all__ = [
    "CACHE_PATH_ENV_KEY",
    "DEFAULT_REPR_LIMIT",
    "IO",
    "NOTHING",
    "REPR_LIMIT_KEY",
    "Annotate",
    "Ask",
    "AsyncRuntime",
    "AtomicGet",
    "AtomicUpdate",
    "Await",
    "CacheDelete",
    "CacheExists",
    "CacheGet",
    "CacheLifecycle",
    "CacheLifecycle",
    "CachePolicy",
    "CachePolicy",
    "CachePut",
    "CacheStorage",
    "CacheStorage",
    "CaptureGraph",
    "Delay",
    "DelayEffect",
    "Effect",
    "EffectCallTree",
    "EffectGenerator",
    "Err",
    "ExecutionContext",
    "FrozenDict",
    "Gather",
    "Get",
    "KleisliProgram",
    "Listen",
    "ListenResult",
    "Local",
    "Log",
    "Maybe",
    "MissingEnvKeyError",
    "Modify",
    "Nothing",
    "Ok",
    "Program",
    "ProgramRunResult",
    "Put",
    "Result",
    "RunResult",
    "Safe",
    "SimulationRuntime",
    "Some",
    "Spawn",
    "Step",
    "StructuredLog",
    "SyncRuntime",
    "Task",
    "Tell",
    "TraceError",
    "WGraph",
    "WNode",
    "WStep",
    "WaitUntil",
    "WaitUntilEffect",
    "annotate",
    "ask",
    "atomic_get",
    "atomic_update",
    "await_",
    "build_graph_snapshot",
    "cache",
    "cache_1hour",
    "cache_1min",
    "cache_5min",
    "cache_delete",
    "cache_exists",
    "cache_forever",
    "cache_get",
    "cache_key",
    "cache_put",
    "capture",
    "capture_graph",
    "clear_persistent_cache",
    "delay",
    "do",
    "get",
    "graph_to_html",
    "graph_to_html_async",
    "io",
    "listen",
    "local",
    "modify",
    "persistent_cache_path",
    "put",
    "run_program",
    "safe",
    "slog",
    "spawn",
    "step",
    "tell",
    "trace_err",
    "wait_until",
    "write_graph_html",
    "write_graph_html_async",
]
