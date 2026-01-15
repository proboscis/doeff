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
    # Avoid shadowing stdlib modules (e.g., ``types``) when the package directory
    # itself is placed on sys.path (as happens with some runpy-based launchers).
    _sys.path = [path for path in _sys.path if path != _PKG_DIR]

# Core types from modular structure
from doeff.types import (
    ExecutionContext,
    RunResult,
    Effect,
    EffectGenerator,
    ListenResult,
    # Repr truncation configuration
    DEFAULT_REPR_LIMIT,
    REPR_LIMIT_KEY,
    # Vendored types
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
    # Effects - Reader
    Ask,
    Local,
    # Effects - State
    Get,
    Put,
    Modify,
    AtomicGet,
    AtomicUpdate,
    # Effects - Writer
    Log,
    StructuredLog,
    Tell,
    Listen,
    # Effects - Future
    Await,
    Thread,
    Spawn,
    Task,
    # Effects - Time
    Delay,
    DelayEffect,
    WaitUntil,
    WaitUntilEffect,
    # Effects - Result
    Fail,
    Finally,
    Catch,
    Recover,
    Safe,
    Unwrap,
    Retry,
    FirstSuccess,
    Unwrap,
    # Effects - IO
    IO,
    Print,
    # Effects - Graph tracking
    Step,
    Annotate,
    Snapshot,
    CaptureGraph,
    # Effects - Dependency injection (pinjected compatible)
    Dep,
    # Effects - Gather for parallel Programs
    Gather,
    MemoGet,
    MemoPut,
    # Effects - Cache
    CacheGet,
    CachePut,
    # Lowercase effect functions (aliases)
    ask,
    local,
    get,
    put,
    modify,
    tell,
    listen,
    slog,
    finally_,
    await_,
    thread,
    spawn,
    delay,
    wait_until,
    fail,
    catch,
    safe,
    recover,
    first_success_effect,
    unwrap_result,
    retry,
    unwrap_result,
    io,
    print_,
    memo_get,
    memo_put,
    step,
    annotate,
    snapshot,
    capture_graph,
    cache_get,
    cache_put,
    atomic_get,
    atomic_update,
)

# Import from modular structure
from doeff.kleisli import KleisliProgram
from doeff.cesk_adapter import CESKInterpreter
from doeff.do import do
from doeff.program import Program
from doeff.graph_snapshot import (
    build_graph_snapshot,
    graph_to_html,
    graph_to_html_async,
    write_graph_html,
    write_graph_html_async,
)

# Import cache decorator
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

# Program runner (CLI-equivalent API)
from doeff.run import run_program, ProgramRunResult

# Runtime: Single-shot Algebraic Effects
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

# Shorthand alias matching lowercase helpers
capture = capture_graph

__all__ = [  # noqa: RUF022
    # Core types
    "Effect",
    "EffectGenerator",
    "Program",
    "ExecutionContext",
    "RunResult",
    "ListenResult",
    "CacheLifecycle",
    "CachePolicy",
    "CacheStorage",
    # Repr truncation configuration
    "DEFAULT_REPR_LIMIT",
    "REPR_LIMIT_KEY",
    # Vendored types
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
    # Core classes
    "KleisliProgram",
    "CESKInterpreter",
    "EffectCallTree",
    # Decorator
    "do",
    # Graph snapshot helpers
    "build_graph_snapshot",
    "graph_to_html",
    "graph_to_html_async",
    "write_graph_html",
    "write_graph_html_async",
    # Effects - Uppercase
    "IO",
    "Annotate",
    "Ask",
    "Await",
    "Delay",
    "DelayEffect",
    "WaitUntil",
    "WaitUntilEffect",
    "Catch",
    "FirstSuccess",
    "Safe",
    "Recover",
    "Unwrap",
    "Retry",
    "Unwrap",
    "Dep",
    "Fail",
    "Gather",
    "MemoGet",
    "MemoPut",
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
    "Finally",
    "Modify",
    "AtomicGet",
    "AtomicUpdate",
    "Thread",
    "Spawn",
    "Task",
    "Print",
    "Put",
    "Step",
    "CaptureGraph",
    "Tell",
    # Effects - lowercase
    "annotate",
    "ask",
    "await_",
    "catch",
    "first_success_effect",
    "safe",
    "recover",
    "unwrap_result",
    "retry",
    "unwrap_result",
    "fail",
    "get",
    "io",
    "listen",
    "local",
    "modify",
    "atomic_get",
    "atomic_update",
    "thread",
    "spawn",
    "delay",
    "wait_until",
    "print_",
    "put",
    "step",
    "capture",
    "capture_graph",
    "tell",
    "slog",
    "finally_",
    "memo_get",
    "memo_put",
    "cache_get",
    "cache_put",
    # Cache decorator
    "CACHE_PATH_ENV_KEY",
    "cache",
    "cache_key",
    "cache_1min",
    "cache_5min",
    "cache_1hour",
    "cache_forever",
    "clear_persistent_cache",
    "persistent_cache_path",
    # Program runner (CLI-equivalent API)
    "run_program",
    "ProgramRunResult",
    # Runtime: Effect handler types and payloads
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
    # Runtime implementations
    "AsyncioRuntime",
    "SyncRuntime",
    "SimulationRuntime",
]
