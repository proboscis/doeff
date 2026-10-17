"""
DoExpr nodes — Rust pyclasses re-exported for Python use.

The VM classifies them via downcast (not tag-based getattr).
"""

import functools
import types
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass
from inspect import CO_VARARGS
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    NamedTuple,
    Protocol,
    TypeVar,
    cast,
    overload,
    runtime_checkable,
)

from doeff_vm import Apply as Apply
from doeff_vm import EffectBase, K
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
from doeff_vm._effect_types import check_handler_annotation

if TYPE_CHECKING:
    from doeff import Program


_Result = TypeVar("_Result")
# The effect a raw handler's effect parameter takes (``handler``): bound to ``EffectBase`` so that
# an annotation no effect can match (``effect: str``) is a type error, as it is a ``TypeError`` at
# run time. A parameter typed ``object`` or ``Any``, or left bare, takes every effect.
_HandledEffect = TypeVar("_HandledEffect", bound=EffectBase)


class ProgramHandler(Protocol):
    """A Program -> Program handler: the program it wraps keeps its result type.

    ``reader(env=...)(prog())`` is a ``Program[T, Any]`` for ``prog()`` a
    ``Program[T, E]``, so ``run`` of it is a ``T``. The effects after
    installing are ``Any``: Python types cannot take the effects the handler
    answers out of ``E``. A value that is not a program — the ``@do`` function
    itself (``writer(prog)``) or ``state()(42)`` — is a type error, as it is a
    ``TypeError`` when the program runs.

    Every installer has this type: the core handlers (``reader``, ``state``,
    ``writer``, ...), ``handler(raw)`` and ``stacked_handlers(...)``. A
    ``defhandler`` handler (``doeff_hy.static_types.Handler``) is one too.
    """

    def __call__(self, program: "Program[_Result, Any]", /) -> "Program[_Result, Any]": ...


@runtime_checkable
class _InstalledHandler(Protocol):
    """The existing installer marker declares a one-Program calling convention.

    Inside this module an installer takes any value: ``with_handlers`` and
    ``stacked_handlers`` apply it to a body typed ``object``, and the VM refuses
    one that is not a program. Callers get it as a ``ProgramHandler``.
    """

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


def _positional_capacity(raw_handler: object) -> int | None:
    """How many positional arguments an unmarked handler value takes, read from attributes only.

    ``None`` = any number (``*args``) or not judged. Never ``inspect.signature``:
    ``with_handlers`` is a hot path (see the marker note in ``handler``), so the
    common shape — a function — is told by its exact type (cheaper than
    ``isinstance`` / ``match``) and judged from two fields of its code object.
    Judged shapes:

    - a plain function, a lambda or a Hy ``fn``: its ``__code__``;
    - a ``@do`` function: the function it wraps (``__doeff_generator_function__``)
      — the wrapper itself takes ``*args``, and the VM calls the wrapped one;
    - a bound method: its ``__func__``, less ``self``;
    - a ``functools.partial`` of one of those: less the positionals it binds,
      cut at the first positional slot it binds by keyword (a positional
      argument reaching that slot would give it a second value).

    Not judged — installed as before and left to the VM: a callable instance,
    a class, a builtin, a partial of one of those, and anything else without
    ``__code__``.
    """
    bound = 0  # leading positional slots already filled: ``self``, a partial's args
    keywords: dict[str, object] | None = None
    current: object = raw_handler
    while True:
        if type(current) is types.FunctionType:
            wrapped = current.__dict__.get("__doeff_generator_function__")
            if wrapped is None:
                code = current.__code__
                if keywords is not None:
                    return _keyword_cut_capacity(code, bound, keywords)
                return None if code.co_flags & CO_VARARGS else code.co_argcount - bound
            current = wrapped
        elif isinstance(current, types.MethodType):
            bound += 1
            current = current.__func__
        elif isinstance(current, functools.partial):
            bound += len(current.args)
            # An outer partial's keywords win over an inner one's (CPython merges plain partials the same way).
            keywords = current.keywords | (keywords or {})
            current = current.func
        else:
            return None


def _keyword_cut_capacity(code: types.CodeType, bound: int, keywords: dict[str, object]) -> int | None:
    """The positional arguments left when a partial binds some parameters by keyword (see ``_positional_capacity``)."""
    names = code.co_varnames
    for position in range(max(bound, code.co_posonlyargcount), code.co_argcount):
        if names[position] in keywords:
            return position - bound
    return None if code.co_flags & CO_VARARGS else code.co_argcount - bound


class _NoProgram:
    """The default of an installer's ``body``: the installer was called with no program."""

    def __repr__(self) -> str:
        return "<no program>"


_NO_PROGRAM = _NoProgram()


def _called_without_program(name: str) -> str:
    """The message of the TypeError for an installer called with no program — a handler
    called as if it were a handler factory (``writer()(prog())`` for ``writer(prog())``)."""
    return (
        f"{name}() got no program: {name} is a handler, not a handler factory. "
        f"Apply it to the program itself: {name}(program), not {name}()(program)"
    )


def _install_raw(
    raw_handler: Callable[..., object], caller: str, name: str | None = None
) -> _InstalledHandler:
    """The installer for an unmarked handler value: a raw ``(effect, k)`` dispatcher.

    A value without the installer marker is called by the VM with
    ``(effect, k)``. One that cannot take two positional arguments is almost
    always a Program -> Program function passed without the marker; it is
    refused here, when the stack is built, instead of failing with a
    ``TypeError`` at the first effect — which a test could miss when the
    failure lands outside the part it checks (agora-redesign #3724).

    The installer called with no program — a handler taken for a factory,
    ``writer()(prog())`` — is refused with a TypeError that names the call
    meant, ``writer(prog())`` (only an identity check on the program's way in).

    An effect annotation no effect can match (``effect: str``) is refused here
    too: the VM would skip the handler for every effect, and the run would end
    in an UnhandledEffect that does not name the annotation
    (``doeff_vm._effect_types.check_handler_annotation``).

    The installer is named after the raw handler (``_handler_label``), or
    ``name`` when given (``_named_handler``).
    """
    taken = _positional_capacity(raw_handler)
    if taken is not None and taken < 2:
        raise TypeError(
            f"{caller}: Program -> Program function without the handler marker: "
            f"{_handler_label(raw_handler).qualname} — build it with defhandler, or bundle "
            f"handlers with stacked_handlers (an unmarked handler is a raw effect dispatcher, "
            f"called with (effect, k); this one takes {taken} positional argument"
            f"{'' if taken == 1 else 's'})"
        )
    check_handler_annotation(raw_handler)

    def install(body: object = _NO_PROGRAM) -> WithHandlerType:
        if body is _NO_PROGRAM:
            raise TypeError(_called_without_program(install.__name__))
        return WithHandlerType(raw_handler, body)

    label = _handler_label(raw_handler)
    install.__name__ = label.name if name is None else name
    install.__qualname__ = label.qualname if name is None else name
    install.__doc__ = label.doc
    install_meta = cast(Any, install)
    install_meta._doeff_is_handler_fn = True
    install_meta.__doeff_handler_data__ = raw_handler
    return cast(_InstalledHandler, install)  # the marker is set on it just above


@overload
def handler(raw_handler: ProgramHandler) -> ProgramHandler: ...


@overload
def handler(raw_handler: Callable[[_HandledEffect, K], object]) -> ProgramHandler: ...


def handler(raw_handler: Callable[..., object]) -> ProgramHandler:
    """Wrap a raw effect dispatcher as a Program -> Program handler.

    ``raw_handler`` may be any callable ``(effect, k) -> Program``: a ``@do``
    function, a plain function, a bound method, a ``functools.partial`` or a
    callable instance. An installer that already carries the handler marker
    is returned as is. An unmarked value that cannot take ``(effect, k)``, or
    whose effect annotation no effect can match (``effect: str``), is refused
    with a ``TypeError`` naming it (see ``_install_raw``).

    The type checker refuses the same mistakes before the program runs: a raw
    handler is typed ``(effect, k: K)``, its effect parameter annotated with an
    ``EffectBase`` class, a union of them, or left open (no annotation,
    ``Any``, ``object``). ``handler(f)`` for ``f(effect: str, k)`` is an error
    in pyright and mypy. A marker base the effects share must derive from
    ``EffectBase`` for the type checker to accept it — one that does not still
    filters at run time, but the type checker cannot tell it from ``str``.
    """
    if not callable(raw_handler):
        raise TypeError(
            f"handler: raw_handler must be callable, got {type(raw_handler).__name__}"
        )
    # The installer marker is read as a plain attribute: ``isinstance`` against the
    # runtime-checkable Protocol walks every member with ``inspect.getattr_static``
    # and doeff-traverse re-wraps every inner handler per item, so the Protocol
    # check dominated the wrap (agora-redesign #2593: 181k checks, ~9% of a
    # screen server test).
    if getattr(raw_handler, "_doeff_is_handler_fn", False) is True:
        return cast(_InstalledHandler, raw_handler)
    return _install_raw(raw_handler, "handler")


def _named_handler(raw_handler: Callable[..., object], name: str) -> ProgramHandler:
    """The installer of a package's ready-made handler, under the name the package exports.

    ``writer`` installs the raw ``_writer_handler``: the installer is named
    ``writer``, so its calls and errors (``writer() got no program``) show the
    name the user wrote. The name is given where the installer is made:
    ``ProgramHandler`` does not declare a function's names, since a partial or
    a ``defhandler`` handler is one too.
    """
    return _install_raw(raw_handler, "handler", name)


@overload
def with_handlers(
    handlers: Iterable[Callable[..., object]], program: "Program[_Result, Any]"
) -> "Program[_Result, Any]": ...


@overload
def with_handlers(handlers: Iterable[Callable[..., object]], program: object) -> "Program[Any]": ...


def with_handlers(handlers: Iterable[Callable[..., object]], program: object) -> object:
    """Apply a handler stack to a Program.

    Handler order is scope order: the first handler is outermost, the last
    handler is innermost. Raw effect dispatchers are normalized through
    ``handler``; handler factories already marked as Program -> Program are
    called directly. Empty runtime lists are accepted as identity so callers can
    compose dynamically discovered stacks.

    A value without the handler marker is a raw dispatcher, called with
    ``(effect, k)``. A Program -> Program function passed without the marker
    (``lambda program: ...``, or a factory returning one) is refused here with
    a ``TypeError`` naming it, before anything runs: make it with
    ``defhandler``, or bundle several handlers into one marked installer with
    ``stacked_handlers``. Shapes whose arguments cannot be read from
    attributes (a callable instance, a builtin) are installed as before.
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
        wrapped = (
            install(wrapped)
            if is_handler_fn is True
            else _install_raw(install, "with_handlers")(wrapped)
        )
    return wrapped


def _bundled_installer(value: Callable[..., object]) -> _InstalledHandler:
    """One handler of a ``stacked_handlers`` bundle as an installer, normalized as ``with_handlers`` does."""
    if not callable(value):
        raise TypeError(f"stacked_handlers: handler must be callable, got {type(value).__name__}")
    if getattr(value, "_doeff_is_handler_fn", False) is True:
        return cast(_InstalledHandler, value)
    return _install_raw(value, "stacked_handlers")


def stacked_handlers(*handlers: Callable[..., object]) -> ProgramHandler:
    """Bundle handlers into one Program -> Program installer.

    ``stacked_handlers(h1, h2)(program)`` is ``with_handlers([h1, h2], program)``:
    the first handler is outermost, the last innermost. Each handler is
    normalized as ``with_handlers`` does, once, when bundling — so an unmarked
    Program -> Program function is refused here. The bundle carries the
    handler marker, so ``with_handlers`` and ``handler`` call it with the
    program only. No handlers bundle into the identity.
    """
    installers = tuple(_bundled_installer(value) for value in handlers)

    def install(body: object) -> "Program[Any]":
        return with_handlers(installers, body)

    install.__name__ = "stacked_handlers"
    install.__qualname__ = "stacked_handlers"
    install.__doc__ = "Handlers stacked as one installer, outermost first: " + ", ".join(
        _handler_label(installer).name for installer in installers
    )
    install_meta = cast(Any, install)
    install_meta._doeff_is_handler_fn = True
    declared = _stacked_declared_marks(handlers)
    if declared is not None:
        install_meta.__doeff_handles__ = declared.handles
        install_meta.__doeff_effects__ = declared.effects
    return install


@dataclass(frozen=True)
class _DeclaredMarks:
    """The declared marks of one handler or one bundle: the effects it answers
    (``__doeff_handles__``) and the effects it performs (``__doeff_effects__``)."""

    handles: tuple[type, ...]
    effects: tuple[type, ...]


def _declared_marks_of(value: object) -> _DeclaredMarks | None:
    """The declared marks of one bundled handler, or ``None`` when it does not
    declare both (it is read from its clauses or not at all)."""
    handles = getattr(value, "__doeff_handles__", None)
    effects = getattr(value, "__doeff_effects__", None)
    if handles is None or effects is None:
        return None
    return _DeclaredMarks(handles=tuple(handles), effects=tuple(effects))


def _stacked_declared_marks(handlers: tuple[Callable[..., object], ...]) -> _DeclaredMarks | None:
    """The ``__doeff_handles__`` / ``__doeff_effects__`` marks of a bundle, so the
    effect analyzer and the foundation closure check read it as declared instead of
    unreadable (agora-redesign #3724 — the same idea as the HTTP handlers copying
    their marks onto the scoped installer).

    Only when every bundled handler declares both marks; otherwise ``None`` and the
    bundle stays unreadable, as an unmarked function is today. Handled = the union
    over the bundle (an effect from the body reaches the innermost handler first and
    climbs). Performed = what each handler performs, minus what a handler outside
    it within the bundle answers — those effects never leave the bundle.
    """
    marks = tuple(_declared_marks_of(value) for value in handlers)
    if any(mark is None for mark in marks):
        return None
    declared = tuple(mark for mark in marks if mark is not None)
    handled = tuple(dict.fromkeys(kind for mark in declared for kind in mark.handles))
    performed = tuple(
        dict.fromkeys(
            kind
            for index, mark in enumerate(declared)
            for kind in mark.effects
            if all(kind not in outer.handles for outer in declared[:index])
        )
    )
    return _DeclaredMarks(handles=handled, effects=performed)


_Answer = TypeVar("_Answer")


def typed_resume(effect: EffectBase[_Answer], k: K, value: _Answer) -> Resume:
    """``Resume(k, value)`` whose value type is checked against the effect.

    For ``class ReadClock(EffectBase[int])``, ``typed_resume(effect, k, "now")``
    is a type error. Runtime behaviour is exactly ``Resume(k, value)``; the
    ``@do`` tail-resume analysis recognises it like ``Resume``.
    """
    return Resume(k, value)


def typed_transfer(effect: EffectBase[_Answer], k: K, value: _Answer) -> Transfer:
    """``Transfer(k, value)`` whose value type is checked against the effect."""
    return Transfer(k, value)


def program(
    gen_fn: Callable[..., Generator[Any, Any, _Result]], *args: object
) -> Expand[_Result]:
    """Wrap a generator function as Expand(Apply(Callable(factory), args)).

    The factory calls gen_fn and wraps the generator as IRStream explicitly.
    """
    from doeff_vm import Callable as VmCallable
    from doeff_vm import IRStream

    def factory(*inner_args: object) -> IRStream:
        gen = gen_fn(*inner_args)
        return IRStream(gen)

    return Expand(Apply(Pure(VmCallable(factory)), [Pure(a) for a in args]))
