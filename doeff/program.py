"""
DoExpr nodes — Rust pyclasses re-exported for Python use.

The VM classifies them via downcast (not tag-based getattr).
"""

from typing import Any, cast

from doeff_vm import Apply as Apply
from doeff_vm import Expand as Expand
from doeff_vm import GetExecutionContext as GetExecutionContext
from doeff_vm import GetHandlers as GetHandlers
from doeff_vm import GetOuterHandlers as GetOuterHandlers
from doeff_vm import GetTraceback as GetTraceback
from doeff_vm import Pass as Pass
from doeff_vm import Perform as Perform
from doeff_vm import Pure as Pure
from doeff_vm import Resume as Resume
from doeff_vm import ResumeThrow as ResumeThrow
from doeff_vm import Transfer as Transfer
from doeff_vm import TransferThrow as TransferThrow
from doeff_vm import WithHandler as WithHandlerType
from doeff_vm import WithObserve as WithObserve


def handler(raw_handler):
    """Wrap a raw effect dispatcher as a Program -> Program handler."""
    if not callable(raw_handler):
        raise TypeError(
            f"handler: raw_handler must be callable, got {type(raw_handler).__name__}"
        )
    raw_handler_meta = cast(Any, raw_handler)
    try:
        is_handler_fn = raw_handler_meta._doeff_is_handler_fn
    except AttributeError:
        is_handler_fn = False
    if is_handler_fn is True:
        return raw_handler

    def install(body):
        return WithHandlerType(raw_handler, body)

    install.__name__ = raw_handler_meta.__name__
    install.__qualname__ = raw_handler_meta.__qualname__
    install.__doc__ = raw_handler_meta.__doc__
    install_meta = cast(Any, install)
    install_meta._doeff_is_handler_fn = True
    install_meta.__doeff_handler_data__ = raw_handler
    return install


def program(gen_fn, *args):
    """Wrap a generator function as Expand(Apply(Callable(factory), args)).

    The factory calls gen_fn and wraps the generator as IRStream explicitly.
    """
    from doeff_vm import Callable as VmCallable
    from doeff_vm import IRStream

    def factory(*inner_args):
        gen = gen_fn(*inner_args)
        return IRStream(gen)

    return Expand(Apply(Pure(VmCallable(factory)), [Pure(a) for a in args]))
