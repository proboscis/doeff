"""
doeff - Algebraic Effects for Python.

Backed by a Rust VM with OCaml 5-aligned effect handler architecture.
"""

# ruff: noqa: I001 - import order avoids doeff_core_effects circular imports.
from collections.abc import Generator
from typing import Any

from doeff_vm import Callable as Callable
from doeff_vm import Callable as _VmCallable
from doeff_vm import EffectBase
from doeff_vm import K as K
from doeff_vm import PyVM as PyVM
from doeff_vm import UnhandledEffect as UnhandledEffect

from doeff.cli.run_services import DoeffRunContext as DoeffRunContext
from doeff.do import do as do
from doeff.mcp import McpParamSchema as McpParamSchema
from doeff.mcp import McpToolDef as McpToolDef
from doeff.program import Apply as Apply
from doeff.program import Expand as Expand
from doeff.program import GetExecutionContext as GetExecutionContext
from doeff.program import GetHandlers as GetHandlers
from doeff.program import GetOuterHandlers as GetOuterHandlers
from doeff.program import GetTraceback as GetTraceback
from doeff.program import Pass as Pass
from doeff.program import Perform as Perform
from doeff.program import Pure as Pure
from doeff.program import Resume as Resume
from doeff.program import ResumeThrow as ResumeThrow
from doeff.program import Transfer as Transfer
from doeff.program import TransferThrow as TransferThrow
from doeff.program import WithHandler as WithHandler
from doeff.program import WithHandlerType as WithHandlerType
from doeff.program import WithObserve as WithObserveRaw
from doeff.program import program as program
from doeff.result import Err as Err
from doeff.result import Maybe as Maybe
from doeff.result import Nothing as Nothing
from doeff.result import Ok as Ok
from doeff.result import Some as Some
from doeff.run import run as run

from doeff_core_effects.effects import Ask as Ask
from doeff_core_effects.effects import Await as Await
from doeff_core_effects.effects import Get as Get
from doeff_core_effects.effects import Listen as Listen
from doeff_core_effects.effects import Local as Local
from doeff_core_effects.effects import Put as Put
from doeff_core_effects.effects import Slog as Slog
from doeff_core_effects.effects import Tell as Tell
from doeff_core_effects.effects import Try as Try
from doeff_core_effects.effects import WriterTellEffect as WriterTellEffect
from doeff_core_effects.effects import slog as slog
from doeff_core_effects.scheduler import PRIORITY_HIGH as PRIORITY_HIGH
from doeff_core_effects.scheduler import PRIORITY_IDLE as PRIORITY_IDLE
from doeff_core_effects.scheduler import PRIORITY_NORMAL as PRIORITY_NORMAL
from doeff_core_effects.scheduler import AcquireSemaphore as AcquireSemaphore
from doeff_core_effects.scheduler import Cancel as Cancel
from doeff_core_effects.scheduler import CompletePromise as CompletePromise
from doeff_core_effects.scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects.scheduler import CreatePromise as CreatePromise
from doeff_core_effects.scheduler import CreateSemaphore as CreateSemaphore
from doeff_core_effects.scheduler import FailPromise as FailPromise
from doeff_core_effects.scheduler import Future as Future
from doeff_core_effects.scheduler import Gather as Gather
from doeff_core_effects.scheduler import Promise as Promise
from doeff_core_effects.scheduler import Race as Race
from doeff_core_effects.scheduler import ReleaseSemaphore as ReleaseSemaphore
from doeff_core_effects.scheduler import Semaphore as Semaphore
from doeff_core_effects.scheduler import Spawn as Spawn
from doeff_core_effects.scheduler import Task as Task
from doeff_core_effects.scheduler import Wait as Wait


def WithObserve(observer, body):  # noqa: N802 - public compatibility constructor
    """Install observer and run body under it.

    Accepts a plain Python callable as observer — automatically wraps it
    with doeff_vm.Callable so the Rust VM can invoke it.

    Use WithObserveRaw if you need the underlying pyclass directly.
    """
    if isinstance(observer, _VmCallable):
        return WithObserveRaw(observer, body)
    if not callable(observer):
        raise TypeError(
            f"WithObserve: observer must be callable, got {type(observer).__name__}"
        )
    return WithObserveRaw(_VmCallable(observer), body)

Effect = EffectBase

# DoExpr — virtual base type for all program nodes.
# Enables isinstance(x, DoExpr) to check if a value is any program node.
_DOEXPR_TYPES = (
    Pure, Perform, Resume, Transfer, Apply, Expand, Pass,
    WithHandlerType, WithObserveRaw, ResumeThrow, TransferThrow,
    GetTraceback, GetExecutionContext, GetHandlers, GetOuterHandlers,
)


class _DoExprMeta(type):
    def __instancecheck__(cls, instance):
        return isinstance(instance, _DOEXPR_TYPES)

    def __subclasscheck__(cls, subclass):
        return issubclass(subclass, _DOEXPR_TYPES)


class DoExpr(metaclass=_DoExprMeta):
    """Virtual base type for all doeff program nodes.

    isinstance(x, DoExpr) returns True for any program node
    (Pure, Expand, WithHandler, etc.).
    """


Program = DoExpr
ProgramBase = DoExpr
AskEffect = Ask


@do
def merge_dicts(*sources) -> Generator[Any, Any, dict]:
    """Monadically merge multiple Program[dict] or plain dicts left-to-right.

    Usage: merge_dicts(Pure({"a": 1}), Pure({"b": 2})) → Program[{"a": 1, "b": 2}]
    """
    merged: dict = {}
    for source in sources:
        if isinstance(source, DoExpr):
            d = yield source
            if not isinstance(d, dict):
                raise TypeError(
                    f"merge_dicts: expected Program[dict] to yield dict, got {type(d).__name__}"
                )
        elif isinstance(source, dict):
            d = source
        else:
            raise TypeError(f"merge_dicts: expected Program[dict] or dict, got {type(source).__name__}")
        merged.update(d)
    return merged

# Removed concepts — raise clear error on use
class _Removed:
    def __init__(self, name, reason):
        self._name = name
        self._reason = reason
    def __call__(self, *a, **kw):
        raise RuntimeError(f"{self._name} was removed: {self._reason}")
    def __getattr__(self, attr):
        raise RuntimeError(f"{self._name} was removed: {self._reason}")

Delegate = _Removed("Delegate", "use 'yield effect' to re-perform in handler body")
EffectGenerator = Generator  # Generator[Any, Any, T] — return type for @do function bodies
WithIntercept = _Removed("WithIntercept", "use WithObserve")
KleisliProgram = _Removed("KleisliProgram", "use @do instead")
MissingEnvKeyError = KeyError
Modify = _Removed("Modify", "use Get + Put")
AllocVar = _Removed("AllocVar", "use var_store directly")
Discontinued = _Removed("Discontinued", "concept removed")
Discontinue = _Removed("Discontinue", "concept removed")
graph_snapshot = _Removed("graph_snapshot", "concept removed")
# NOTHING — use Nothing (lowercase singleton) instead
ReadVar = _Removed("ReadVar", "use var_store directly")
CacheGet = _Removed("CacheGet", "renamed to MemoGet in doeff_core_effects.memo_effects")
CacheExists = _Removed("CacheExists", "renamed to MemoExists in doeff_core_effects.memo_effects")
cache = _Removed("cache", "cache module removed")
presets = _Removed("presets", "presets module removed")
rust_vm = _Removed("rust_vm", "use PyVM directly")
race = Race  # lowercase alias

default_handlers = _Removed("default_handlers", "compose handlers by calling handler(program)")
async_run = _Removed("async_run", "use run() with scheduled()")
default_async_handlers = _Removed(
    "default_async_handlers", "compose handlers by calling handler(program)"
)

__version__ = "0.4.1"
