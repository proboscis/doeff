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
    WithHandler as _WithHandlerNode,
    ResumeThrow,
    TransferThrow,
    WithObserve,
    GetTraceback,
    GetExecutionContext,
    GetHandlers,
    GetOuterHandlers,
)

WithHandlerType = _WithHandlerNode


def WithHandler(h, body, *args, **kwargs):
    """Install handler ``h`` around ``body``.

    Accepts two forms:

    - New-style (preferred): ``h`` is a function ``Program -> Program`` marked
      with ``_doeff_is_handler_fn = True``. The call is forwarded as ``h(body)``.
    - Legacy: ``h`` is a raw handler dispatcher (an ``@do``-decorated
      ``fn[effect, k]``). Falls through to the Rust ``WithHandler`` pyclass.

    The legacy path is kept so that pre-migration code keeps working.
    New code should build handlers via ``defhandler``/``handle`` and invoke
    them as plain functions: ``(h body)`` in Hy, ``h(body)`` in Python.
    """
    if getattr(h, "_doeff_is_handler_fn", False):
        return h(body, *args, **kwargs)
    return _WithHandlerNode(h, body, *args, **kwargs)


def program(gen_fn, *args):
    """Wrap a generator function as Expand(Apply(Callable(factory), args)).

    The factory calls gen_fn and wraps the generator as IRStream explicitly.
    """
    from doeff_vm import Callable as VmCallable, IRStream

    def factory(*inner_args):
        gen = gen_fn(*inner_args)
        return IRStream(gen)

    return Expand(Apply(Pure(VmCallable(factory)), [Pure(a) for a in args]))
