"""
DoExpr nodes — Rust pyclasses re-exported for Python use.

The VM classifies them via downcast (not tag-based getattr).
"""

import functools
import types
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, Protocol, cast, runtime_checkable

from doeff_vm import Apply as Apply
from doeff_vm import Expand as Expand
from doeff_vm import GetBoundaries as GetBoundaries
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

if TYPE_CHECKING:
    from doeff import Program


ProgramHandler = Callable[[object], "Program"]


@runtime_checkable
class _InstalledHandler(Protocol):
    """The existing installer marker declares a one-Program calling convention."""

    @property
    def _doeff_is_handler_fn(self) -> Literal[True]: ...

    def __call__(self, body: object) -> "Program": ...


class _HandlerLabel(NamedTuple):
    name: str
    qualname: str
    doc: str | None


def _handler_label(raw_handler: object) -> _HandlerLabel:
    """Name, qualname and doc for an installer, for any callable handler.

    Plain and ``@do`` functions carry their own names. ``functools.partial``
    and callable instances do not have ``__name__``; they are labelled by the
    function they call (partial) or by their class (callable instance), so
    every callable is accepted as a handler — the VM only needs it callable.
    """
    if isinstance(raw_handler, functools.partial):
        return _handler_label(raw_handler.func)
    if isinstance(raw_handler, (types.FunctionType, types.BuiltinFunctionType)):
        return _HandlerLabel(raw_handler.__name__, raw_handler.__qualname__, raw_handler.__doc__)
    if isinstance(raw_handler, types.MethodType):
        return _handler_label(raw_handler.__func__)
    handler_type = type(raw_handler)
    return _HandlerLabel(handler_type.__name__, handler_type.__qualname__, handler_type.__doc__)


def handler(raw_handler: Callable[..., object]) -> ProgramHandler:
    """Wrap a raw effect dispatcher as a Program -> Program handler.

    ``raw_handler`` may be any callable ``(effect, k) -> Program``: a ``@do``
    function, a plain function, a bound method, a ``functools.partial`` or a
    callable instance.
    """
    if not callable(raw_handler):
        raise TypeError(
            f"handler: raw_handler must be callable, got {type(raw_handler).__name__}"
        )
    if isinstance(raw_handler, _InstalledHandler) and raw_handler._doeff_is_handler_fn is True:
        return raw_handler

    def install(body: object) -> WithHandlerType:
        return WithHandlerType(raw_handler, body)

    label = _handler_label(raw_handler)
    install.__name__ = label.name
    install.__qualname__ = label.qualname
    install.__doc__ = label.doc
    install_meta = cast(Any, install)
    install_meta._doeff_is_handler_fn = True
    install_meta.__doeff_handler_data__ = raw_handler
    return install


def with_handlers(handlers: Iterable[ProgramHandler], program: object) -> object:
    """Apply a handler stack to a Program.

    Handler order is scope order: the first handler is outermost, the last
    handler is innermost. Raw effect dispatchers are normalized through
    ``handler``; handler factories already marked as Program -> Program are
    called directly. Empty runtime lists are accepted as identity so callers can
    compose dynamically discovered stacks.
    """
    wrapped = program
    for install in reversed(tuple(handlers)):
        if not callable(install):
            raise TypeError(
                f"with_handlers: handler must be callable, got {type(install).__name__}"
            )
        install_meta = cast(Any, install)
        try:
            is_handler_fn = install_meta._doeff_is_handler_fn
        except AttributeError:
            is_handler_fn = False
        wrapped = install(wrapped) if is_handler_fn is True else handler(install)(wrapped)
    return wrapped


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
