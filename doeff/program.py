"""
DoExpr nodes — Rust pyclasses re-exported for Python use.

The VM classifies them via downcast (not tag-based getattr).
"""

import functools
import types
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass
from inspect import CO_GENERATOR, CO_VARARGS
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


def _is_do_function(value: object) -> bool:
    """Whether ``value`` is a ``@do`` function: the wrapper ``@do`` returns, carrying
    ``__doeff_generator_function__`` (``doeff.do.program_factory``), seen through the bindings that
    keep it a ``@do`` Kleisli — a bound method of a ``@do`` method (``self`` or ``cls`` bound) and a
    ``functools.partial`` of a ``@do`` function (arguments bound). A plain function, a lambda and a
    callable instance are not (ADR-DOE-CORE-003). Read from attributes only, as
    ``_positional_capacity`` is (``with_handlers`` is a hot path)."""
    current: object = value
    while True:
        if type(current) is types.FunctionType:
            return "__doeff_generator_function__" in current.__dict__
        if isinstance(current, types.MethodType):
            current = current.__func__
        elif isinstance(current, functools.partial):
            current = current.func
        else:
            return False


def _yields_its_control(function: Callable[..., object]) -> Callable[..., object]:
    """``function`` refusing to hand back a program node or an effect as its value.

    The body of a handler written without ``yield`` is a ``@do`` function that returns a value:
    the value ends the handled block. ``return Resume(k, v)`` there hands the ``Resume`` back as
    that value instead of resuming — the form a plain-function handler of 0.7.0 used, where the
    VM ran the returned node. The node is refused here with the form that runs it,
    ``return (yield Resume(k, v))`` (ADR-DOE-CORE-003 R1).
    """
    from doeff import DoExpr, EffectBase  # doeff imports this module, so read them when called

    @functools.wraps(function)
    def answer(*args: object, **kwargs: object) -> object:
        value = function(*args, **kwargs)
        if isinstance(value, (DoExpr, EffectBase)):
            node = type(value).__name__
            raise TypeError(
                f"{_handler_label(function).qualname} returned {node}(...) without yielding it. "
                f"A handler body is a @do function: yield the {node} to run it and return what "
                f"it gives back — return (yield {node}(...)) (ADR-DOE-CORE-003)"
            )
        return value

    return answer


def _as_do_function(raw_handler: object, caller: str) -> Callable[..., object]:
    """The ``@do`` function a handler runs: ``raw_handler`` itself when it is one, or the
    ``def`` written under ``@handler`` made one here.

    ``@handler`` makes the ``def`` under it a ``@do`` function, so ``@do`` is not written
    under it (ADR-DOE-CORE-003 R1 — the owner's decisions 2026-10-20 13:07 JST "@doがついて
    いない関数をdoeffは基本的に受け付けるべきではない" and 16:26 JST "@handlerだけを許可して、
    @handler def...と書けるようにして、@handler@doはdeprecatedにしよう"). A ``@do`` function,
    or a partial or bound method of one, is taken as it is. A lambda, a callable instance and
    any other callable are refused with a TypeError: a handler is a ``def``.
    """
    if _is_do_function(raw_handler):
        return cast(Callable[..., object], raw_handler)  # _is_do_function saw a function, or a partial or method of one
    if type(raw_handler) is types.FunctionType and raw_handler.__name__ != "<lambda>":
        from doeff.do import do  # doeff.do imports this module

        generator = raw_handler.__code__.co_flags & CO_GENERATOR
        return do(raw_handler if generator else _yields_its_control(raw_handler))
    raise TypeError(
        f"{caller}: {_handler_label(raw_handler).qualname} is not a handler body. A handler is "
        "a def (effect, k) marked with @handler, which makes it a @do function "
        "(ADR-DOE-CORE-003)"
    )


def _refuse_unmarked(value: object, caller: str) -> None:
    """Refuse a value without the installer marker in a handler stack (ADR-DOE-CORE-003 R2).

    ``with_handlers`` and ``stacked_handlers`` take handlers only — installers made by
    ``@handler`` on a def, by ``defhandler``, or a ``stacked_handlers`` bundle. They
    do not turn anything else into a handler on the way in.
    """
    if not callable(value):
        raise TypeError(f"{caller}: handler must be callable, got {type(value).__name__}")
    if getattr(value, "_doeff_is_handler_fn", False) is True:
        return
    raise TypeError(
        f"{caller}: {_handler_label(value).qualname} is not a handler. Make it with @handler on "
        "its def (effect, k), or with defhandler, before putting it in a handler stack "
        "(ADR-DOE-CORE-003)"
    )


def _install_raw(
    raw_handler: Callable[..., object], caller: str, name: str | None = None
) -> _InstalledHandler:
    """The installer for a handler body ``(effect, k)``: what ``@handler`` makes of it.

    The body is a ``def`` (made a ``@do`` function here) or a ``@do`` function; anything else
    is refused first (``_as_do_function``). The ``@do`` function is called by the VM with
    ``(effect, k)``. The checks below read the body as written, before it is wrapped. One that cannot take two positional arguments is almost
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
    body = _as_do_function(raw_handler, caller)
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

    def install(program: object = _NO_PROGRAM) -> WithHandlerType:
        if program is _NO_PROGRAM:
            raise TypeError(_called_without_program(install.__name__))
        return WithHandlerType(body, program)

    label = _handler_label(raw_handler)
    install.__name__ = label.name if name is None else name
    install.__qualname__ = label.qualname if name is None else name
    install.__doc__ = label.doc
    install_meta = cast(Any, install)
    install_meta._doeff_is_handler_fn = True
    install_meta.__doeff_handler_data__ = body
    return cast(_InstalledHandler, install)  # the marker is set on it just above


@overload
def handler(raw_handler: ProgramHandler) -> ProgramHandler: ...


@overload
def handler(raw_handler: Callable[[_HandledEffect, K], object]) -> ProgramHandler: ...


def handler(raw_handler: Callable[..., object]) -> ProgramHandler:
    """Make a ``def`` ``(effect, k)`` a Program -> Program handler: ``@handler`` above the def.

    ``@handler`` makes the def a ``@do`` function, so its body yields effects and control
    like any ``@do`` function, and ``@do`` is not written under it (ADR-DOE-CORE-003 —
    ``@handler`` over ``@do`` is deprecated, and refused in this repo by semgrep). A ``@do``
    function, or a ``functools.partial`` or bound method of one, is taken as it is, and an
    installer that already carries the handler marker is returned as is. A lambda, a
    callable instance or any other callable is refused with a ``TypeError`` here, before the
    program runs. A body that cannot take ``(effect, k)``, or whose effect annotation no
    effect can match (``effect: str``), is refused the same way (see ``_install_raw``).

    A body without ``yield`` ends the handled block with the value it returns. Returning a
    control node unrun — ``return Resume(k, v)`` — is refused when it happens: write
    ``return (yield Resume(k, v))``, which resumes ``k`` and returns what the rest of the
    program gave back.

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
    handler is innermost. Empty runtime lists are accepted as identity so callers can
    compose dynamically discovered stacks.

    Every element must be a handler: an installer made by ``@handler`` on a
    def, by ``defhandler``, or a ``stacked_handlers`` bundle. A value
    without the handler marker — a plain function, a ``@do`` function without
    ``@handler``, a lambda — is refused with a ``TypeError`` naming it before
    anything runs; it is not turned into a handler on the way in
    (ADR-DOE-CORE-003 R2).
    """
    stack = tuple(handlers)
    for install in stack:
        _refuse_unmarked(install, "with_handlers")
    wrapped = program
    for install in reversed(stack):
        wrapped = install(wrapped)
    return wrapped


def _bundled_installer(value: Callable[..., object]) -> _InstalledHandler:
    """One handler of a ``stacked_handlers`` bundle: a handler already, as ``with_handlers`` requires."""
    _refuse_unmarked(value, "stacked_handlers")
    return cast(_InstalledHandler, value)


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
