"""
The @do decorator — converts a generator function into a program factory.

    @do
    def my_program(x) -> EffectGenerator[int]:
        result = yield some_effect
        return result + x

    prog = my_program(42)  # returns Program[int] (not executed yet)
    result = run(prog)     # execute
"""
from __future__ import annotations

import inspect
from collections.abc import Generator
from functools import wraps
from typing import Any, Callable, ParamSpec, TypeVar

from doeff.program import Expand, Apply, Pure

P = ParamSpec("P")
T = TypeVar("T")


def do(fn: Callable[P, Generator[Any, Any, T]]) -> Callable[P, Expand[T]]:
    """Wrap a generator function so calling it returns a DoExpr tree."""
    from doeff_vm import Callable as VMCallable, IRStream

    def _make_stream(result):
        if inspect.isgenerator(result):
            return IRStream(result)
        def value_gen():
            if False:
                yield
            return result
        return IRStream(value_gen())

    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Expand[T]:
        def thunk():
            return _make_stream(fn(*args, **kwargs))
        return Expand(Apply(Pure(VMCallable(thunk)), []))
    return wrapper
