"""
doeff - Algebraic Effects for Python.

Backed by a Rust VM with OCaml 5-aligned effect handler architecture.
"""

from collections.abc import Generator
from typing import Any, Callable, ParamSpec, TypeVar

from doeff.do import do
from doeff.program import (
    Apply,
    Expand,
    GetExecutionContext,
    GetHandlers,
    GetTraceback,
    Pass,
    Perform,
    Pure,
    Resume,
    Transfer,
    WithHandler,
    WithObserve,
    program,
)
from doeff.program import ResumeThrow, TransferThrow
from doeff.run import run
from doeff.result import Ok, Err, Some, Nothing, Maybe  # noqa: F811
from doeff_vm import Callable, EffectBase, K, PyVM

Effect = EffectBase

# --- Compat re-exports (old API names) ---
# Effects (now in doeff_core_effects.effects)
from doeff_core_effects.effects import (  # noqa: E402
    Ask, Get, Put, Local, Listen, Await, Try,
    WriterTellEffect, Slog, slog, Tell,
)
# Scheduler effects (now in doeff_core_effects.scheduler)
from doeff_core_effects.scheduler import (  # noqa: E402
    Spawn, Gather, Wait, Race, Cancel,
    CreatePromise, CompletePromise, FailPromise,
    CreateSemaphore, AcquireSemaphore, ReleaseSemaphore,
    CreateExternalPromise,
    Task, Future, Promise, Semaphore,
    PRIORITY_IDLE, PRIORITY_NORMAL, PRIORITY_HIGH,
)

# Type aliases
Program = Expand  # @do functions return Expand nodes
ProgramBase = Expand
AskEffect = Ask

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
CacheGet = _Removed("CacheGet", "cache effects removed")
CacheExists = _Removed("CacheExists", "cache effects removed")
cache = _Removed("cache", "cache module removed")
presets = _Removed("presets", "presets module removed")
rust_vm = _Removed("rust_vm", "use PyVM directly")
race = Race  # lowercase alias

default_handlers = _Removed("default_handlers", "compose handlers explicitly with WithHandler")
async_run = _Removed("async_run", "use run() with scheduled()")
default_async_handlers = _Removed("default_async_handlers", "compose handlers explicitly with WithHandler")

__version__ = "0.2.1"
