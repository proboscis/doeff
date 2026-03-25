"""
DoExpr nodes — pure Python, no Rust base classes.

The VM classifies Python objects by reading their `tag` attribute.
"""

from typing import Any


class Pure:
    """Return a value. tag=0"""
    tag = 0
    def __init__(self, value: Any) -> None:
        self.value = value


class Perform:
    """Perform an effect. tag=5"""
    tag = 5
    def __init__(self, effect: Any) -> None:
        self.effect = effect


class Resume:
    """Resume continuation with value (non-tail). tag=6"""
    tag = 6
    def __init__(self, k: Any, value: Any) -> None:
        self.continuation = k
        self.value = value


class Transfer:
    """Resume continuation with value (tail). tag=7"""
    tag = 7
    def __init__(self, k: Any, value: Any) -> None:
        self.continuation = k
        self.value = value


class Apply:
    """Call f(args). tag=16"""
    tag = 16
    def __init__(self, f: Any, args: list) -> None:
        self.f = f
        self.args = args


class Expand:
    """Evaluate inner expr to Stream, then run it. tag=17"""
    tag = 17
    def __init__(self, expr: Any) -> None:
        self.expr = expr


class ResumeThrow:
    """Throw exception into continuation (non-tail). tag=21"""
    tag = 21
    def __init__(self, k: Any, exception: Any) -> None:
        self.continuation = k
        self.exception = exception


class TransferThrow:
    """Throw exception into continuation (tail). tag=22"""
    tag = 22
    def __init__(self, k: Any, exception: Any) -> None:
        self.continuation = k
        self.exception = exception


class Pass:
    """Inner handler doesn't handle, forward to outer. tag=19"""
    tag = 19
    def __init__(self, effect: Any, k: Any) -> None:
        self.effect = effect
        self.continuation = k


class WithHandler:
    """Install handler and run body under it. tag=20"""
    tag = 20
    def __init__(self, handler: Any, body: Any) -> None:
        self.handler = handler
        self.body = body


class WithObserve:
    """Install observer and run body under it. tag=24
    Observer is called synchronously on every effect. Return value ignored.
    """
    tag = 24
    def __init__(self, observer: Any, body: Any) -> None:
        self.observer = observer
        self.body = body


class GetTraceback:
    """Query traceback from a continuation without consuming it. tag=23"""
    tag = 23
    def __init__(self, k: Any) -> None:
        self.continuation = k


class GetExecutionContext:
    """Get current execution context (traceback from current position). tag=25"""
    tag = 25


def program(gen_fn, *args):
    """Wrap a generator function as Expand(Apply(Callable(factory), args)).

    The factory calls gen_fn and wraps the generator as IRStream explicitly.
    """
    from doeff_vm import Callable as VmCallable, IRStream

    def factory(*inner_args):
        gen = gen_fn(*inner_args)
        return IRStream(gen)

    return Expand(Apply(Pure(VmCallable(factory)), [Pure(a) for a in args]))
