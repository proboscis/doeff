"""
The @do decorator — converts a generator function into a program factory.

    @do
    def my_program(x):
        result = yield some_effect
        return result + x

    prog = my_program(42)  # returns DoExpr (not executed yet)
    result = run(prog)     # execute
"""

import inspect
from functools import wraps

from doeff.program import Expand, Apply, Pure


def do(fn):
    """Wrap a generator function so calling it returns a DoExpr tree."""
    from doeff_vm import Callable, IRStream

    def _make_stream(result):
        if inspect.isgenerator(result):
            return IRStream(result)
        def value_gen():
            if False:
                yield
            return result
        return IRStream(value_gen())

    @wraps(fn)
    def wrapper(*args, **kwargs):
        def thunk():
            return _make_stream(fn(*args, **kwargs))
        return Expand(Apply(Pure(Callable(thunk)), []))
    return wrapper
