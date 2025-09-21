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

# Core types from modular structure
from doeff.types import (
    ExecutionContext,
    RunResult,
    Effect,
    EffectGenerator,
    ListenResult,
    # Vendored types
    Ok,
    Err,
    Result,
    TraceError,
    trace_err,
    WNode,
    WStep,
    WGraph,
    FrozenDict,
)

from doeff.effects import (
    # Effects - Reader
    Ask,
    Local,
    # Effects - State
    Get,
    Put,
    Modify,
    # Effects - Writer
    Log,
    Tell,
    Listen,
    # Effects - Future
    Await,
    Parallel,
    # Effects - Result
    Fail,
    Catch,
    Recover,
    Safe,
    Unwrap,
    Retry,
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
    GatherDict,
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
    await_,
    parallel,
    fail,
    catch,
    safe,
    recover,
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
)

# Import from new modular structure
from doeff.program import Program
from doeff.interpreter import ProgramInterpreter
from doeff.kleisli import KleisliProgram
from doeff.do import do
from doeff.graph_snapshot import (
    build_graph_snapshot,
    graph_to_html,
    graph_to_html_async,
    write_graph_html,
    write_graph_html_async,
)

# Import cache decorator
from doeff.cache import (
    cache,
    cache_key,
    cache_1min,
    cache_5min,
    cache_1hour,
    cache_forever,
)
from doeff.cache_policy import CacheLifecycle, CachePolicy, CacheStorage

__version__ = "0.1.0"

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
    # Vendored types
    "Ok",
    "Err",
    "Result",
    "TraceError",
    "trace_err",
    "WNode",
    "WStep",
    "WGraph",
    "FrozenDict",
    # Core classes
    "ProgramInterpreter",
    "KleisliProgram",
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
    "Catch",
    "Safe",
    "Recover",
    "Unwrap",
    "Retry",
    "Unwrap",
    "Dep",
    "Fail",
    "Gather",
    "GatherDict",
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
    "Modify",
    "Parallel",
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
    "parallel",
    "print_",
    "put",
    "step",
    "capture",
    "capture_graph",
    "tell",
    "memo_get",
    "memo_put",
    "cache_get",
    "cache_put",
    # Cache decorator
    "cache",
    "cache_key",
    "cache_1min",
    "cache_5min",
    "cache_1hour",
    "cache_forever",
]
