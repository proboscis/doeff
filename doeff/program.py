"""
DoExpr nodes — Rust pyclasses re-exported for Python use.

The VM classifies them via downcast (not tag-based getattr).
"""

from doeff_vm import (
    Pure,
    Perform,
    Resume,
    Transfer,
    Apply,
    Expand,
    Pass,
    WithHandler,
    ResumeThrow,
    TransferThrow,
    WithObserve,
    GetTraceback,
    GetExecutionContext,
    GetHandlers,
    GetOuterHandlers,
)


def program(gen_fn, *args):
    """Wrap a generator function as Expand(Apply(Callable(factory), args)).

    The factory calls gen_fn and wraps the generator as IRStream explicitly.
    """
    from doeff_vm import Callable as VmCallable, IRStream

    def factory(*inner_args):
        gen = gen_fn(*inner_args)
        return IRStream(gen)

    return Expand(Apply(Pure(VmCallable(factory)), [Pure(a) for a in args]))
