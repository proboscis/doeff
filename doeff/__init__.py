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

from typing import Any, cast

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
from doeff.effects import (
    PRIORITY_HIGH,
    PRIORITY_IDLE,
    PRIORITY_NORMAL,
    AcquireSemaphore,
    AcquireSemaphoreEffect,
    Annotate,
    Ask,
    AskEffect,
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
    CreateSemaphore,
    CreateSemaphoreEffect,
    ExternalPromise,
    FailPromise,
    Future,
    Gather,
    GatherEffect,
    Get,
    Listen,
    Local,
    Log,
    Modify,
    Promise,
    Put,
    Race,
    RaceResult,
    ReleaseSemaphore,
    ReleaseSemaphoreEffect,
    Semaphore,
    Snapshot,
    Spawn,
    SpawnEffect,
    Step,
    StructuredLog,
    Task,
    Tell,
    Try,
    Wait,
    WriterTellEffect,
    acquire_semaphore,
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
    create_semaphore,
    gather,
    get,
    listen,
    local,
    modify,
    put,
    race,
    release_semaphore,
    slog,
    snapshot,
    spawn,
    step,
    tell,
    try_,
    wait,
)
from doeff.errors import Discontinued, MissingEnvKeyError
from doeff.graph_snapshot import (
    build_graph_snapshot,
    graph_to_html,
    graph_to_html_async,
    write_graph_html,
    write_graph_html_async,
)
from doeff.handlers.cache_handlers import (
    cache_handler,
    content_address,
    in_memory_cache_handler,
    make_memo_rewriter,
    memo_rewriters,
    sqlite_cache_handler,
)
from doeff.kleisli import KleisliProgram
from doeff.program import Program, ProgramBase
from doeff.rust_vm import (
    WithHandler,
    WithIntercept,
    async_run,
    default_async_handlers,
    default_handlers,
    run,
)
from doeff.types import (
    DEFAULT_REPR_LIMIT,
    NOTHING,
    REPR_LIMIT_KEY,
    Effect,
    EffectBase,
    EffectGenerator,
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
    "Pure",
    "Apply",
    "Expand",
    "Eval",
    "EvalInScope",
    "Perform",
    "Discontinue",
    "Pass",
    "Resume",
    "Delegate",
    "Transfer",
    "ResumeContinuation",
    "GetScopeOf",
    "PushScope",
    "PopScope",
    "AllocVar",
    "ReadVar",
    "WriteVar",
    "WriteVarNonlocal",
    "K",
}

# G18/G19: Unified types that accept both Rust VM and Python instances.
# isinstance(rust_ok, doeff.Ok) and isinstance(python_ok, doeff.Ok) both work.
_VM_UNIFIED_NAMES = {"Ok", "Err"}


def _build_unified_types():
    """Build unified Ok/Err that recognize both Rust and Python instances."""
    from doeff import types as _t

    py_types: dict[str, type[Any] | None] = {
        "Ok": getattr(_t, "Ok", None),
        "Err": getattr(_t, "Err", None),
    }
    from doeff_vm import doeff_vm as _ext

    rust_types: dict[str, type[Any] | None] = {
        "Ok": getattr(_ext, "Ok", None),
        "Err": getattr(_ext, "Err", None),
    }

    unified: dict[str, type[Any] | None] = {}
    for name in ("Ok", "Err"):
        candidates = cast(
            tuple[type[Any], ...],
            tuple(t for t in (rust_types.get(name), py_types.get(name)) if isinstance(t, type)),
        )
        if len(candidates) <= 1:
            unified[name] = candidates[0] if candidates else None
        else:

            class _UnifiedMeta(type):
                _types: tuple[type[Any], ...] = candidates

                def __instancecheck__(cls, instance):
                    return isinstance(instance, cast(tuple[type[Any], ...], cls._types))

                def __subclasscheck__(cls, subclass):
                    return issubclass(subclass, cast(tuple[type[Any], ...], cls._types))

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
    "NOTHING",
    "PRIORITY_HIGH",
    "PRIORITY_IDLE",
    "PRIORITY_NORMAL",
    "REPR_LIMIT_KEY",
    "AcquireSemaphore",
    "AcquireSemaphoreEffect",
    "Annotate",
    "Apply",
    "Ask",
    "AskEffect",
    "AtomicGet",
    "AtomicUpdate",
    "Await",
    "CacheDelete",
    "CacheExists",
    "CacheGet",
    "CacheLifecycle",
    "CachePolicy",
    "CachePut",
    "CacheStorage",
    "CaptureGraph",
    "CompletePromise",
    "CreateExternalPromise",
    "CreatePromise",
    "CreateSemaphore",
    "CreateSemaphoreEffect",
    "Delegate",
    "Discontinue",
    "Discontinued",
    "Effect",
    "EffectBase",
    "EffectCallTree",
    "EffectGenerator",
    "Err",
    "Eval",
    "EvalInScope",
    "Expand",
    "ExternalPromise",
    "FailPromise",
    "FrozenDict",
    "Future",
    "Gather",
    "GatherEffect",
    "Get",
    "K",
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
    "Pass",
    "Perform",
    "Program",
    "ProgramBase",
    "Promise",
    "Pure",
    "Put",
    "Race",
    "RaceResult",
    "ReleaseSemaphore",
    "ReleaseSemaphoreEffect",
    "Result",
    "Resume",
    "ResumeContinuation",
    "GetScopeOf",
    "PushScope",
    "PopScope",
    "AllocVar",
    "ReadVar",
    "WriteVar",
    "WriteVarNonlocal",
    "RunResult",
    "Semaphore",
    "Snapshot",
    "Some",
    "Spawn",
    "SpawnEffect",
    "Step",
    "StructuredLog",
    "Task",
    "Tell",
    "TraceError",
    "Transfer",
    "Try",
    "WGraph",
    "WNode",
    "WStep",
    "Wait",
    "WithHandler",
    "WithIntercept",
    "WriterTellEffect",
    "acquire_semaphore",
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
    "cache_handler",
    "cache_key",
    "cache_put",
    "capture",
    "capture_graph",
    "clear_persistent_cache",
    "content_address",
    "create_semaphore",
    "default_async_handlers",
    "default_handlers",
    "do",
    "gather",
    "get",
    "graph_to_html",
    "graph_to_html_async",
    "in_memory_cache_handler",
    "listen",
    "local",
    "make_memo_rewriter",
    "memo_rewriters",
    "modify",
    "persistent_cache_path",
    "put",
    "race",
    "release_semaphore",
    "run",
    "slog",
    "snapshot",
    "spawn",
    "sqlite_cache_handler",
    "step",
    "tell",
    "trace_err",
    "try_",
    "wait",
    "write_graph_html",
    "write_graph_html_async",
]
