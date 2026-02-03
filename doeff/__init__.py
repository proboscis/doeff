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
from doeff.cesk.run import (
    async_handlers_preset,
    async_run,
    sync_handlers_preset,
    sync_run,
)
from doeff.do import do
from doeff.effects import (
    Annotate,
    Ask,
    Promise,
    AtomicGet,
    AtomicUpdate,
    Await,
    CacheDelete,
    CacheExists,
    CacheGet,
    CachePut,
    CaptureGraph,
    CompletePromise,
    CreatePromise,
    FailPromise,
    Future,
    Gather,
    Get,
    Intercept,
    Listen,
    Local,
    Log,
    Modify,
    Put,
    Race,
    RaceResult,
    Safe,
    Snapshot,
    Spawn,
    Step,
    StructuredLog,
    Task,
    Tell,
    Wait,
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
    get,
    listen,
    local,
    modify,
    put,
    race,
    safe,
    slog,
    snapshot,
    spawn,
    step,
    tell,
    wait,
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
    "NOTHING",
    "REPR_LIMIT_KEY",
    "Annotate",
    "Ask",
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
    "CompletePromise",
    "CreatePromise",
    "Effect",
    "EffectCallTree",
    "EffectGenerator",
    "Err",
    "ExecutionContext",
    "FailPromise",
    "FrozenDict",
    "Future",
    "Gather",
    "Get",
    "Intercept",
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
    "Promise",
    "Put",
    "Race",
    "RaceResult",
    "Result",
    "RunResult",
    "Safe",
    "Some",
    "Spawn",
    "Step",
    "StructuredLog",
    "Task",
    "Tell",
    "TraceError",
    "WGraph",
    "WNode",
    "WStep",
    "Wait",
    "annotate",
    "ask",
    "async_handlers_preset",
    "async_run",
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
    "do",
    "get",
    "graph_to_html",
    "graph_to_html_async",
    "listen",
    "local",
    "modify",
    "persistent_cache_path",
    "put",
    "race",
    "run_program",
    "safe",
    "slog",
    "spawn",
    "step",
    "sync_handlers_preset",
    "sync_run",
    "tell",
    "trace_err",
    "wait",
    "write_graph_html",
    "write_graph_html_async",
]
