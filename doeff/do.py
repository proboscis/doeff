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

    @wraps(fn)
    def factory(*args):
        result = fn(*args)
        if inspect.isgenerator(result):
            return IRStream(result)
        # Non-generator @do function — wrap return value as a trivial generator
        def value_gen():
            if False:
                yield
            return result
        return IRStream(value_gen())

    @wraps(fn)
    def wrapper(*args):
        return Expand(Apply(Pure(Callable(factory)), [Pure(a) for a in args]))
    return wrapper
