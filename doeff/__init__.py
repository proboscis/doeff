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
    Retry,
    # Effects - IO
    IO,
    Print,
    # Effects - Graph tracking
    Step,
    Annotate,
    Snapshot,
    # Effects - Dependency injection (pinjected compatible)
    Dep,
    # Effects - Gather for parallel Programs
    Gather,
    GatherDict,
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
    recover,
    retry,
    io,
    print_,
    step,
    annotate,
    snapshot,
    cache_get,
    cache_put,
)

# Import from new modular structure
from doeff.program import Program
from doeff.interpreter import ProgramInterpreter
from doeff.kleisli import KleisliProgram
from doeff.do import do
from doeff.webui_stream import stream_program_to_webui

# Import cache decorator
from doeff.cache import (
    cache,
    cache_key,
    cache_1min,
    cache_5min,
    cache_1hour,
    cache_forever,
)

__version__ = "0.1.0"

__all__ = [  # noqa: RUF022
    # Core types
    "Effect",
    "EffectGenerator",
    "Program",
    "ExecutionContext",
    "RunResult",
    "ListenResult",
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
    # Web UI helper
    "stream_program_to_webui",
    # Effects - Uppercase
    "IO",
    "Annotate",
    "Ask",
    "Await",
    "Catch",
    "Recover",
    "Retry",
    "Dep",
    "Fail",
    "Gather",
    "GatherDict",
    "CacheGet",
    "CachePut",
    "Get",
    "Listen",
    "Local",
    "Log",
    "Modify",
    "Parallel",
    "Print",
    "Put",
    "Step",
    "Tell",
    # Effects - lowercase
    "annotate",
    "ask",
    "await_",
    "catch",
    "recover",
    "retry",
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
    "tell",
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
