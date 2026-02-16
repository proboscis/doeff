"""
doeff - Algebraic Effects for Python.

An algebraic effects system with one-shot continuations, backed by a Rust VM.
Uses generators for do-notation and ships with batteries-included effect handlers:
Reader, State, Writer, Future, Result, IO, Cache, and more.

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
from doeff.do import do
from doeff.errors import MissingEnvKeyError
from doeff.effects import (
    Annotate,
    Ask,
    AskEffect,
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
    CreateExternalPromise,
    CreatePromise,
    ExternalPromise,
    FailPromise,
    Future,
    Gather,
    GatherEffect,
    Get,
    Intercept,
    Listen,
    Local,
    Log,
    Modify,
    Put,
    ProgramTrace,
    ProgramTraceEffect,
    Race,
    RaceResult,
    Safe,
    Snapshot,
    Spawn,
    SpawnEffect,
    Step,
    StructuredLog,
    Task,
    Tell,
    WriterTellEffect,
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
    gather,
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
from doeff.nonblocking_await import (
    nonblocking_await_handler,
    with_nonblocking_await,
)
from doeff.graph_snapshot import (
    build_graph_snapshot,
    graph_to_html,
    graph_to_html_async,
    write_graph_html,
    write_graph_html_async,
)
from doeff.kleisli import KleisliProgram
from doeff.program import DoCtrl, DoExpr, GeneratorProgram, Program, ProgramBase
from doeff.run import ProgramRunResult, run_program
from doeff.rust_vm import (
    async_run,
    async_run_with_handler_map,
    default_handlers,
    run,
    run_with_handler_map,
    wrap_with_handler_map,
)
from doeff.types import (
    DEFAULT_REPR_LIMIT,
    NOTHING,
    REPR_LIMIT_KEY,
    Effect,
    EffectBase,
    EffectGenerator,
    ExecutionContext,
    FrozenDict,
    ListenResult,
    Maybe,
    Nothing,
    Result,
    RunResult,
    Some,
    TraceError,
    WGraph,
    WNode,
    WStep,
    trace_err,
)

__version__ = "0.2.1"

capture = capture_graph

# G8: lazy re-exports of VM dispatch primitives from doeff_vm
_VM_LAZY_EXPORTS = {
    "WithHandler",
    "Pure",
    "Call",
    "Eval",
    "Perform",
    "Resume",
    "Delegate",
    "Transfer",
    "ResumeContinuation",
    "K",
}

# G18/G19: Unified types that accept both Rust VM and Python instances.
# isinstance(rust_ok, doeff.Ok) and isinstance(python_ok, doeff.Ok) both work.
_VM_UNIFIED_NAMES = {"Ok", "Err"}


def _build_unified_types():
    """Build unified Ok/Err that recognize both Rust and Python instances."""
    from doeff import types as _t

    py_types = {
        "Ok": getattr(_t, "Ok", None),
        "Err": getattr(_t, "Err", None),
    }
    rust_types: dict = {}
    try:
        from doeff_vm import doeff_vm as _ext

        rust_types = {
            "Ok": getattr(_ext, "Ok", None),
            "Err": getattr(_ext, "Err", None),
        }
    except ImportError:
        pass

    unified = {}
    for name in ("Ok", "Err"):
        candidates = tuple(t for t in (rust_types.get(name), py_types.get(name)) if t is not None)
        if len(candidates) <= 1:
            unified[name] = candidates[0] if candidates else None
        else:

            class _UnifiedMeta(type):
                _types = candidates

                def __instancecheck__(cls, instance):
                    return isinstance(instance, cls._types)

                def __subclasscheck__(cls, subclass):
                    return issubclass(subclass, cls._types)

            unified[name] = _UnifiedMeta(name, (), {"_types": candidates})
    return unified


def __getattr__(name: str):
    if name in _VM_LAZY_EXPORTS:
        import doeff_vm

        obj = getattr(doeff_vm, name)
        globals()[name] = obj
        return obj
    if name in _VM_UNIFIED_NAMES:
        _unified = _build_unified_types()
        globals().update(_unified)
        return _unified[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CACHE_PATH_ENV_KEY",
    "DEFAULT_REPR_LIMIT",
    "Delegate",
    "K",
    "NOTHING",
    "REPR_LIMIT_KEY",
    "Annotate",
    "Ask",
    "AskEffect",
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
    "CreateExternalPromise",
    "CreatePromise",
    "DoCtrl",
    "DoExpr",
    "Effect",
    "EffectBase",
    "EffectCallTree",
    "EffectGenerator",
    "Err",
    "ExecutionContext",
    "ExternalPromise",
    "FailPromise",
    "FrozenDict",
    "Future",
    "Gather",
    "GatherEffect",
    "Get",
    "GeneratorProgram",
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
    "nonblocking_await_handler",
    "Ok",
    "Program",
    "ProgramBase",
    "ProgramRunResult",
    "Promise",
    "Put",
    "ProgramTrace",
    "ProgramTraceEffect",
    "Race",
    "RaceResult",
    "Result",
    "Pure",
    "Call",
    "Eval",
    "Perform",
    "Resume",
    "ResumeContinuation",
    "RunResult",
    "Safe",
    "Some",
    "Spawn",
    "SpawnEffect",
    "Step",
    "StructuredLog",
    "Task",
    "Tell",
    "WriterTellEffect",
    "Transfer",
    "TraceError",
    "WGraph",
    "WithHandler",
    "WNode",
    "WStep",
    "Wait",
    "annotate",
    "ask",
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
    "default_handlers",
    "get",
    "gather",
    "graph_to_html",
    "graph_to_html_async",
    "listen",
    "local",
    "modify",
    "persistent_cache_path",
    "put",
    "race",
    "run_program",
    "run",
    "run_with_handler_map",
    "safe",
    "slog",
    "spawn",
    "step",
    "tell",
    "trace_err",
    "async_run_with_handler_map",
    "wrap_with_handler_map",
    "wait",
    "with_nonblocking_await",
    "write_graph_html",
    "write_graph_html_async",
]
