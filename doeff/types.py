"""Compat: doeff.types — re-exports from new locations."""
from __future__ import annotations
from typing import Any, Generator, TypeVar

from doeff_vm import Ok, Err

T = TypeVar("T")
EffectGenerator = Generator[Any, Any, T]

# Result type compat
class Result:
    """Compat: Result is now Ok/Err from doeff_vm."""
    Ok = Ok
    Err = Err

class RunResult:
    """Compat stub — new VM returns plain values, not RunResult."""
    def __init__(self, value=None, error=None):
        self._value = value
        self._error = error

    @property
    def value(self):
        return self._value

    @property
    def error(self):
        return self._error

    def is_ok(self):
        return self._error is None

    def is_err(self):
        return self._error is not None

    def display(self):
        if self.is_ok():
            return f"Ok({self._value})"
        return f"Err({self._error})"
