"""
doeff - Algebraic Effects for Python.

Backed by a Rust VM with OCaml 5-aligned effect handler architecture.
"""

from doeff.do import do
from doeff.program import (
    Apply,
    Delegate,
    Expand,
    GetTraceback,
    Pass,
    Perform,
    Pure,
    Resume,
    Transfer,
    WithHandler,
    program,
)
from doeff.run import run
from doeff_vm import Callable, EffectBase, Err, K, Ok, PyVM

__version__ = "0.2.1"

__all__ = [
    "Apply",
    "Callable",
    "Delegate",
    "EffectBase",
    "Err",
    "Expand",
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
    "do",
    "program",
    "run",
]
