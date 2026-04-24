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


_NEW_STYLE_DEPRECATION_MSG = (
    "WithHandler(h, body) is deprecated: h is already a Program -> Program "
    "function produced by defhandler, call it directly as h(body) "
    "(or in Hy: (h body)). The shim stays in place indefinitely for "
    "backward compatibility but emits this warning to steer new code."
)

_LEGACY_DEPRECATION_MSG = (
    "WithHandler(h, body) with a raw @do-dispatcher ``h`` is deprecated: "
    "rewrite the handler with defhandler (Hy) or @handler-style factory "
    "that returns a Program -> Program function, then call it as h(body). "
    "The shim stays in place indefinitely for backward compatibility but "
    "emits this warning to steer new code toward the PR A1 idiom."
)


def WithHandler(h, body, *args, **kwargs):
    """Install handler ``h`` around ``body`` — **deprecated**.

    New-style handlers built with ``defhandler`` are already Program →
    Program functions; prefer calling them directly::

        # Before
        WithHandler(my_handler, program)
        # After
        my_handler(program)

    Accepts two forms for backward compatibility:

    - New-style: ``h`` is a function ``Program -> Program`` marked with
      ``_doeff_is_handler_fn = True``. The call is forwarded as ``h(body)``.
      Emits :class:`DeprecationWarning`.
    - Legacy: ``h`` is a raw ``@do``-decorated dispatcher ``fn[effect, k]``.
      Falls through to the Rust ``WithHandler`` pyclass. Emits
      :class:`DeprecationWarning` pointing at ``defhandler``.

    The shim itself is permanent (scope A) — the warning is informational
    and does not break existing code.
    """
    import warnings

    if getattr(h, "_doeff_is_handler_fn", False):
        warnings.warn(_NEW_STYLE_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
        return h(body, *args, **kwargs)
    warnings.warn(_LEGACY_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
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
