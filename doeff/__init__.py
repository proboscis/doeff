"""
doeff - Algebraic Effects for Python.

Backed by a Rust VM with OCaml 5-aligned effect handler architecture.
"""

from doeff.do import do
from doeff.program import (
    Apply,
    Expand,
    GetExecutionContext,
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
from doeff.run import run
from doeff_vm import Callable, EffectBase, Err, K, Ok, PyVM

# ---------------------------------------------------------------------------
# Compat layer — re-export core-effects symbols for legacy code
# ---------------------------------------------------------------------------
from typing import Generator, Any, TypeVar as _TypeVar

_T = _TypeVar("_T")
EffectGenerator = Generator[Any, Any, _T]  # type alias for @do return type

from doeff_core_effects import (
    Ask, Get, Put, Tell, Try, Slog, WriterTellEffect,
    Local, Listen, Await, slog,
)
from doeff_core_effects.scheduler import Spawn, Wait, Gather, Race, Cancel
from doeff_core_effects.cache import cache

Effect = EffectBase
WithIntercept = WithObserve
Program = object  # type: ignore

def ask(key: str):
    """Compat: ask("key") → Ask("key") as a Program-like placeholder."""
    return Ask(key)

def default_handlers():
    """Compat: return core handler list."""
    from doeff_core_effects.handlers import (
        reader, state, writer, try_handler, slog_handler,
        local_handler, listen_handler, await_handler,
    )
    return [reader, state, writer, try_handler, slog_handler,
            local_handler, listen_handler, await_handler]

__version__ = "0.2.1"

__all__ = [
    "Apply",
    "Callable",
    "EffectBase",
    "Err",
    "Expand",
    "GetExecutionContext",
    "GetTraceback",
    "K",
    "Ok",
    "Pass",
    "Perform",
    "Pure",
    "PyVM",
    "Resume",
    "Transfer",
    "WithHandler",
    "WithObserve",
    "do",
    "program",
    "run",
]
