"""
doeff - Algebraic Effects for Python.

Backed by a Rust VM with OCaml 5-aligned effect handler architecture.
"""

from doeff.do import do
from doeff.program import (
    Apply,
    Delegate,
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

# Deprecated alias — prefer DoExpr node types directly
Program = object  # type: ignore — any DoExpr node is a "Program"

__version__ = "0.2.1"

__all__ = [
    "Apply",
    "Callable",
    "Delegate",
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
