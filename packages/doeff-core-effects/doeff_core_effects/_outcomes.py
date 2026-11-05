"""Runtime of absence and failure (ADR-DOE-CORE-EFFECTS-003) -- the declaration of an effect's answers, the opening that ``<-`` yields, and the boundary handlers.

Vocabulary:

- ``Absent`` / ``Raise`` (``doeff_core_effects._effects``) -- an expected absence (the Maybe side) and failure (the Result side).
- ``Outcomes`` -- a declaration that splits an effect's answer type into success, absence, failure, and value. Built from the head dictionary of ``defeffect``'s
  ``:absent`` / ``:failure`` / ``:value``, and left in the effect type's attribute ``__doeff_outcomes__`` (R5).
- ``open_bind`` -- returns what ``(<- x e)`` and ``(! e)`` yield. For an effect with a declaration, a Program that turns an absent answer into ``Absent`` and
  a failure answer into ``Raise(answer)`` and emits it in the caller's scope; for a Result / Maybe value, a Program that opens it.
  Anything else (an effect with no declaration, a Program) returns **what it received as is** -- it yields the same thing as before (R6).
- Boundary handlers (effect -> fold into a value): ``maybe``, ``result``, ``on_raise``, ``absent_as`` (R2, R7, R8).
  ``maybe`` and ``result`` with no settings are applied as handler values, ``maybe(p)`` and ``result(p)`` (ADR-DOE-API-002 R1 -- the old call
  ``maybe()`` returns the same handler with a DeprecationWarning for one version), and ``on_raise`` and ``absent_as``, which take settings, are applied
  as factories that return a handler, ``on_raise(cases...)(p)`` and ``absent_as(default)(p)`` (ADR-DOE-API-001 R6).
  They do not resume the continuation (they end the scope with that value). The only time resuming is allowed is when ``absent_as`` resumes an
  ``Absent`` emitted by a ``<-`` inside its own literal text.

Handlers answer with values and the conversion is done by ``<-`` in the caller's body (R4) -- because emitting ``Absent`` / ``Raise`` inside a handler clause
would go outside that handler and jump over the caller's ``maybe`` / ``result``.
"""

import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from types import GeneratorType, NoneType, UnionType
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Literal,
    Never,
    Protocol,
    TypeAlias,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    overload,
)

from doeff import BindOpener, Call, EffectBase, Err, Expand, IRStream, Ok, Pure
from doeff.do import do
from doeff.program import Pass, ProgramHandler, Transfer, _called_as_former_factory
from doeff.program import handler as _program_handler
from doeff.result import Maybe, Nothing, Some, _NothingType
from doeff_core_effects._effects import Absent, Raise

if TYPE_CHECKING:
    from typing_extensions import deprecated

    # doeff's top level reaches this module while loading doeff_core_effects._effects, so names defined in the latter half of the top level
    # (DoExpr, EffectGenerator) are used only in type annotations (string annotations -- not evaluated at runtime).
    from doeff import DoExpr, EffectGenerator, Program

    # What can be passed to ``<-``: a Program, an effect, or a Result / Maybe value.
    Bindable: TypeAlias = DoExpr | Program[Any, Any] | EffectBase[Any] | Ok[Any] | Err[Any] | Some[Any] | _NothingType
else:

    def deprecated(message: str) -> Callable[[Callable[..., object]], Callable[..., object]]:
        """Stands in for the runtime ``typing_extensions.deprecated``: type checking reads the real one, and the handler emits the runtime warning."""
        return lambda function: function


_D = TypeVar("_D")


# ---------------------------------------------------------------------------
# Answer declaration (R5)
# ---------------------------------------------------------------------------


class Outcome(Enum):
    """The meaning of one effect answer."""

    SUCCESS = "success"  # the rest of the declaration -- <- binds the content
    ABSENT = "absent"  # <- emits Absent
    FAILURE = "failure"  # <- emits Raise(answer)
    VALUE = "value"  # an answer treated normally in the business (a version loss, etc.) -- <- binds the content (a marker that it is not declared a failure)


def _members(answer: object) -> list[type]:
    """Turn an answer type (one type or a union) into a list of element types -- to check whether a declared element is in :answer. ``None`` is ``NoneType``."""
    if answer is None:
        return [NoneType]
    if isinstance(answer, UnionType) or get_origin(answer) is Union:
        return [member for arg in get_args(answer) for member in _members(arg)]
    if isinstance(answer, type):
        return [answer]
    raise TypeError(f"An answer type must be a type or a union: {answer!r}")


def _declared_types(where: str, listed: tuple[object, ...]) -> list[type]:
    """The list of types written for one of :absent / :failure / :value (None is NoneType; overlaps are a TypeError)."""
    out = [member for item in listed for member in _members(item)]
    if len(set(out)) != len(out):
        raise TypeError(f"{where} has the same type twice: {out!r}")
    return out


@dataclass(frozen=True)
class Outcomes:
    """A declaration that splits an effect's answer type into success, absence, failure, and value (R5 -- not inferred from type names).

    ``absent`` / ``failure`` / ``value`` are elements of the answer union and do not overlap each other. The remaining elements are ``success``.
    """

    effect: str
    success: tuple[type, ...]
    absent: tuple[type, ...]
    failure: tuple[type, ...]
    value: tuple[type, ...]

    @classmethod
    def declare(
        cls,
        effect: str,
        answer: object,
        absent: tuple[object, ...],
        failure: tuple[object, ...],
        value: tuple[object, ...],
    ) -> "Outcomes":
        """Build the declaration from defeffect's :answer and :absent / :failure / :value (a mismatch is a TypeError)."""
        members = _members(answer)
        declared = {
            ":absent": _declared_types(f"{effect}'s :absent", absent),
            ":failure": _declared_types(f"{effect}'s :failure", failure),
            ":value": _declared_types(f"{effect}'s :value", value),
        }
        seen: dict[type, str] = {}
        for key, types in declared.items():
            for member in types:
                if member not in members:
                    raise TypeError(
                        f"{member.__name__} in {key} of {effect} is not an element of :answer"
                        f"(:answer = {' | '.join(m.__name__ for m in members)})"
                    )
                if member in seen:
                    raise TypeError(f"{effect}: {member.__name__} is in both {seen[member]} and {key}")
                seen[member] = key
        return cls(
            effect=effect,
            success=tuple(m for m in members if m not in seen),
            absent=tuple(declared[":absent"]),
            failure=tuple(declared[":failure"]),
            value=tuple(declared[":value"]),
        )

    @property
    def converts(self) -> bool:
        """Whether <- can convert the answer (whether at least one absent or failure type is declared)."""
        return bool(self.absent or self.failure)

    def classify(self, answer: object) -> Outcome:
        """The meaning of one answer value."""
        if self.absent and isinstance(answer, self.absent):
            return Outcome.ABSENT
        if self.failure and isinstance(answer, self.failure):
            return Outcome.FAILURE
        if self.value and isinstance(answer, self.value):
            return Outcome.VALUE
        return Outcome.SUCCESS


def outcomes_of(effect_type: type) -> Outcomes | None:
    """The answer declaration of an effect type (None if not declared)."""
    declared = getattr(effect_type, "__doeff_outcomes__", None)
    if declared is None or isinstance(declared, Outcomes):
        return declared
    raise TypeError(f"{effect_type.__name__}.__doeff_outcomes__ must be an Outcomes: {declared!r}")


# ---------------------------------------------------------------------------
# Marker of absent-as's literal text (R8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class AbsentAsToken:
    """Marker of one evaluation of absent-as. Compared by identity (the macro absent-as makes one on every evaluation)."""


@dataclass(frozen=True)
class DirectBind:
    """The ``<-`` / ``!`` expression written directly inside absent-as's literal text (wrapped by the macro absent-as).

    ``open_bind`` opens this and marks the ``Absent`` it emits -- absent-as resumes only that Absent with the default value.
    """

    token: AbsentAsToken
    expr: object


@dataclass(frozen=True)
class BoundAbsent(Absent):
    """The Absent (marked) that a ``<-`` inside absent-as's literal text emitted in place of a value. To other receivers it is an ordinary Absent."""

    token: AbsentAsToken = field(compare=False, repr=False)


@dataclass(frozen=True)
class AbsentDefault(Generic[_D]):
    """The value passed when absent-as resumes a BoundAbsent (the opening side checks that the marker matches)."""

    token: AbsentAsToken
    value: _D


_ABSENT_RESUMED = (
    "Absent was resumed -- the only thing that can resume Absent is absent-as, and only for what a <- inside absent-as's literal text"
    " emitted (ADR-DOE-CORE-EFFECTS-003 R8). The resuming handler should either discard the continuation (finish) or pass it outward (reperform)"
)
_RAISE_RESUMED = (
    "Raise was resumed -- nobody resumes Raise (because it would continue as if a failure had succeeded;"
    " ADR-DOE-CORE-EFFECTS-003 R1). The receiving handler should end the scope with finish or pass it outward (reperform)"
)


# ---------------------------------------------------------------------------
# Opening (value -> effect) -- what <- and ! yield (R6)
# ---------------------------------------------------------------------------


@do
def _absent(why: str, token: AbsentAsToken | None) -> "EffectGenerator[object]":
    """Emit Absent (BoundAbsent if marked). Return only absent-as's resume value; any other resumption is an error."""
    if token is None:
        yield Absent(why)
        raise RuntimeError(_ABSENT_RESUMED)
    resumed = yield BoundAbsent(why, token)
    if isinstance(resumed, AbsentDefault) and resumed.token is token:
        return resumed.value
    raise RuntimeError(_ABSENT_RESUMED)


@do
def _raise(reason: object) -> "EffectGenerator[Never]":
    """Emit Raise(reason). Being resumed is an error."""
    yield Raise(reason)
    raise RuntimeError(_RAISE_RESUMED)


@do
def _open_declared(effect: EffectBase, outcomes: Outcomes, token: AbsentAsToken | None) -> "EffectGenerator[object]":
    """Emit an effect that has a declaration, and open the answer according to the declaration (success and value -> the content, absence -> Absent, failure -> Raise(answer))."""
    answer = yield effect
    match outcomes.classify(answer):
        case Outcome.ABSENT:
            return (yield _absent(f"The answer of {outcomes.effect} is absent: {answer!r}", token))
        case Outcome.FAILURE:
            return (yield _raise(answer))
        case Outcome.SUCCESS | Outcome.VALUE:
            return answer


@do
def _open_value(value: Ok | Err | Some | _NothingType, token: AbsentAsToken | None) -> "EffectGenerator[object]":
    """Open a Result / Maybe value: Ok / Some -> the content, Err(reason) -> Raise(reason), Nothing -> Absent.

    If the content of Err is a Python exception (an implementation error that Try folded), raise the exception as is without making it a Raise (R11 -- do not mix).
    """
    match value:
        case Ok():
            return value.value
        case Some():
            return value.value
        case Err() if isinstance(value.error, BaseException):
            raise value.error
        case Err():
            return (yield _raise(value.error))
        case _NothingType():
            return (yield _absent("Nothing was opened with <-", token))


@do
def _perform_unresumable(effect: Absent | Raise) -> "EffectGenerator[Never]":
    """Emit the (<- (Absent ...)) / (<- (Raise e)) written directly in the body. Being resumed is an error (do not silently continue)."""
    yield effect
    raise RuntimeError(_ABSENT_RESUMED if isinstance(effect, Absent) else _RAISE_RESUMED)


_VALUE_TYPES: frozenset[type] = frozenset({Ok, Err, Some, _NothingType})


def _settled(call: Call) -> object:
    """The ``Pure`` of the answer from calling on the spot an @do call that emits no effects (a function with no yield in its body -- a defk judgment and the like).

    Even if the VM ran that call, it would just call the function and return the answer as is (the body emits nothing), so calling it
    at the binding site means the same -- the call time (the binding site), the position where the exception is raised, and the checks of ``:pre`` / ``:post`` do not change, and only the VM round trip
    disappears. A call to a function with a yield in its body is the ``call`` itself (the VM runs it).
    If a function with no yield returns a generator, that generator is run as a Program, the same as the VM.
    """
    definition = call.function
    if definition.yields:
        return call
    value = definition.function(*call.args, **call.kwargs)
    if type(value) is GeneratorType:
        return Expand(Pure(IRStream(value)))
    return Pure(value)


def _opened(expr: object, token: AbsentAsToken | None) -> object:
    """The Program that opens expr (expr itself if it is not something to open)."""
    if isinstance(expr, Call):
        return _settled(expr)
    kind = type(expr)
    if kind is DirectBind:
        return _opened(expr.expr, expr.token)
    if kind in _VALUE_TYPES:
        return _open_value(expr, token)
    outcomes = getattr(kind, "__doeff_outcomes__", None)
    if outcomes is not None and outcomes.converts:
        return _open_declared(expr, outcomes, token)
    if isinstance(expr, (Absent, Raise)):
        return _perform_unresumable(expr)
    return expr


def _open_bind(expr: object, absent: Callable[[], object] | None = None) -> object:
    """What ``(<- x expr)`` / ``(! expr)`` yields (the Python path of ``open_bind``, which the single point of doeff-hy's <- expansion calls).

    - an effect that has a declaration (``__doeff_outcomes__``) -> a Program that opens the answer according to the declaration
    - Ok / Err / Some / Nothing -> a Program that opens it
    - Absent / Raise -> a Program that emits it and makes it an error if resumed
    - a call of an @do that emits no effects (a function with no yield in its body) -> the ``Pure`` of the answer called on the spot (``_settled``).
      When doeff-hy's binding receives a ``Pure`` it binds ``.value`` without yielding (omitting the VM round trip -- #844).
      Yielding gives the same answer (``Pure`` is a Program that returns that answer)
    - anything else (an effect with no declaration, a Program) -> **expr itself** (yields the same thing as before)
    - if ``absent`` is passed, that one binding is wrapped in a narrow receiver, and an Absent emitted inside is turned into ``Raise(absent())``
      (``<-``'s ``:absent <failure>``). The receiver is inside the caller's body, so that Raise reaches the caller's boundary.
    """
    opened = _opened(expr, None)
    if absent is None:
        return opened
    return absent_raises(absent)(_performed(opened))


def _passes_through(kind: type) -> bool:
    """When an operand of type kind is bound without ``absent``, whether ``_open_bind`` returns it as is (it hits none of the dispatches of ``_opened`` --
    a call, DirectBind, a value type, an effect with a converting declaration, or Absent / Raise). The answer is determined by the type alone, so
    ``BindOpener`` remembers it per type and does not hand the binding of operands of that type to the Python dispatch."""
    if issubclass(kind, (Call, Absent, Raise)) or kind is DirectBind or kind in _VALUE_TYPES:
        return False
    outcomes = getattr(kind, "__doeff_outcomes__", None)
    return outcomes is None or not outcomes.converts


def _generator_program(value: GeneratorType) -> object:
    """The Program for when an @do function that emits no effects returned a generator (runs that generator the same as the VM -- the same shape as ``_settled``)."""
    return Expand(Pure(IRStream(value)))


# The single point of binding. The hot path (binding, without ``absent``, a ``Call`` of an @do that emits no effects -- a defk judgment) is called on the spot inside Rust
# by doeff-vm's ``BindOpener``, which returns ``Pure``, and everything else is handed to ``_open_bind`` (the answer is the same;
# for a judgment bound 10 to 16 times per screen line, the Python dispatch open_bind -> _opened -> _settled was
# the largest remaining cost of one binding). Also, for a call of an @do that emits effects, without ``absent`` (a ``Call`` with a yield in its body),
# ``BindOpener`` returns that call itself -- the same answer as ``_settled`` returning the call as is (
# in the custody contract's example, 13.8k of the 16.3k bindings that reached the Python dispatch were this shape). When binding, without ``absent``, an effect with no
# converting declaration (a type for which ``_passes_through`` is true), ``BindOpener`` also returns that effect itself using the answer remembered per type
# (binding effects per request of the record service used to reach the Python dispatch every time).
# doeff-hy's <- expansion and effect-analyzer look this object up by name.
open_bind: BindOpener = BindOpener(_open_bind, _generator_program, _passes_through)


@do
def _performed(expr: object) -> "EffectGenerator[object]":
    """The Program that yields expr and returns the answer (to make the body of a boundary handler a DoExpr)."""
    return (yield expr)


# ---------------------------------------------------------------------------
# Folding (effect -> value) -- boundary handlers (R2, R7, R8)
# ---------------------------------------------------------------------------


@do
def _absent_to_nothing(effect: EffectBase, k: object) -> "EffectGenerator[object]":
    """On receiving Absent, discard the continuation and end the scope with Nothing. Pass everything else outward."""
    if isinstance(effect, Absent):
        return Nothing
    yield Pass(effect, k)


@do
def _raise_to_err(effect: EffectBase, k: object) -> "EffectGenerator[object]":
    """On receiving Raise(reason), discard the continuation and end the scope with Err(reason). Pass everything else outward."""
    if isinstance(effect, Raise):
        return Err(effect.reason)
    yield Pass(effect, k)


class BoundaryHandler(Protocol):
    """The type of boundary handlers (``maybe``, ``result``, and what ``on_raise(...)`` and ``absent_as(...)`` return).

    A handler with the marker ``_doeff_is_handler_fn``, applied as ``h1(h2(p))``, and can also be placed in ``with_handlers`` and ``stacked_handlers``.
    program may be anything that can be passed to ``<-`` (even an effect with a declaration passed as is is opened and then folded), and the answer type may differ from the wrapped
    program (maybe gives Some / Nothing, result gives Ok / Err), so the type is kept separate from ``ProgramHandler``, which preserves the answer type.
    """

    _doeff_is_handler_fn: Literal[True] = True

    def __call__(self, program: "Bindable", /) -> "DoExpr": ...


class FormerFactoryBoundaryHandler(BoundaryHandler, Protocol):
    """The type of a boundary handler with no settings (``maybe``, ``result``) -- it used to be a factory with no arguments (ADR-DOE-API-002 R3).

    Applied to a program as ``maybe(p)``, and placed in a list as ``maybe`` itself. For one version, the old call ``maybe()``
    emits a DeprecationWarning and returns the same handler (type checking also says deprecated -- the same shape as doeff.program's ``FormerFactoryHandler``
    ).
    """

    @overload
    def __call__(self, program: "Bindable", /) -> "DoExpr": ...

    @overload
    @deprecated(
        "This handler has no settings, so it is not a handler factory: write the handler itself, "
        "without the call (ADR-DOE-API-002)"
    )
    def __call__(self) -> "FormerFactoryBoundaryHandler": ...


_NO_PROGRAM: Any = object()


def _boundary(name: str, clauses: ProgramHandler, opened: Callable[[object], object]) -> BoundaryHandler:
    """Boundary handler: open program with ``opened`` and wrap it, and place ``clauses`` (clauses that receive Absent or Raise) around it.

    The marker and the clause functions (``__doeff_handler_data__`` -- where doeff-effect-analyzer reads the clauses) are taken from those of ``clauses``.
    """

    def install(program: "Bindable") -> "DoExpr":
        return clauses(opened(program))

    return _marked(install, name, clauses)


def _former_factory_boundary(
    name: str, clauses: ProgramHandler, opened: Callable[[object], object], doc: str
) -> FormerFactoryBoundaryHandler:
    """The shape of a handler (``maybe``, ``result``) that used to be a factory with no arguments, for ``_boundary``: the call without a program, ``maybe()``,
    emits a DeprecationWarning for one version and returns the handler itself (ADR-DOE-API-002 R3)."""

    def install(program: "Bindable" = _NO_PROGRAM) -> object:
        if program is _NO_PROGRAM:
            warnings.warn(_called_as_former_factory(name), DeprecationWarning, stacklevel=2)
            return install
        return clauses(opened(program))

    install.__doc__ = doc
    return cast(FormerFactoryBoundaryHandler, _marked(install, name, clauses))


def _marked(install: Callable[..., object], name: str, clauses: ProgramHandler) -> BoundaryHandler:
    install.__name__ = name
    install.__qualname__ = name
    install_meta = cast(Any, install)  # put marker fields on the function (untyped fields -- the same placement as doeff.program's installer)
    install_meta._doeff_is_handler_fn = True
    install_meta.__doeff_handler_data__ = cast(Any, clauses).__doeff_handler_data__  # read the untyped field that the handler places
    return cast(BoundaryHandler, install)  # the marker was placed just above


@do
def _some_of(program: object) -> "EffectGenerator[Maybe]":
    return Some((yield open_bind(program)))


@do
def _ok_of(program: object) -> "EffectGenerator[Ok]":
    return Ok((yield open_bind(program)))


def _opened_only(program: object) -> object:
    """Only open the program (return the folded answer as is -- the body of on_raise and absent_as)."""
    return _performed(open_bind(program))


maybe: FormerFactoryBoundaryHandler = _former_factory_boundary(
    "maybe",
    _program_handler(_absent_to_nothing),
    _some_of,
    """``(maybe body)`` / ``maybe(body)`` -- a handler that folds Absent into Nothing and success into Some (Raise passes through).

    It has no settings, so it is a handler value rather than a factory (ADR-DOE-API-002 R1). body may be anything that can be passed to ``<-``
    (even an effect with a declaration passed as is is opened and then folded). The old call ``maybe()`` emits a DeprecationWarning for one version
    and returns the same handler.
    """,
)

result: FormerFactoryBoundaryHandler = _former_factory_boundary(
    "result",
    _program_handler(_raise_to_err),
    _ok_of,
    """``(result body)`` / ``result(body)`` -- a handler that folds Raise(reason) into Err(reason) and success into Ok (Absent passes through).

    It has no settings, so it is a handler value rather than a factory (ADR-DOE-API-002 R1). The nesting order chooses the combined type:
    ``result(maybe(b))`` gives Ok(Some v), Ok(Nothing), Err(e); ``maybe(result(b))`` gives Some(Ok v), Some(Err e), Nothing (R2).
    The old call ``result()`` emits a DeprecationWarning for one version and returns the same handler.
    """,
)


@dataclass(frozen=True)
class RaiseCase:
    """One case of on-raise: map a Raise of reason type ``reasons`` to a business answer with ``recover``.

    ``recover`` returns Some(answer) or Nothing (the type matched but the shape does not -- go to the next case). A type that accepts anything
    (object, exception types) cannot be placed (R7) -- the macro on-raise builds this from the pattern's type.
    """

    reasons: tuple[type, ...]
    recover: Callable[[object], Maybe]

    def __post_init__(self) -> None:
        if not self.reasons:
            raise TypeError("RaiseCase.reasons cannot be empty")
        for reason in self.reasons:
            if not isinstance(reason, type):
                raise TypeError(f"Elements of RaiseCase.reasons must be types: {reason!r}")
            if reason is object or issubclass(reason, BaseException):
                raise TypeError(
                    f"on-raise cannot receive {reason.__name__} -- a catch-all form and Python exceptions are"
                    " not received (ADR-DOE-CORE-EFFECTS-003 R7). Name the failure type"
                )


def on_raise(*cases: RaiseCase) -> BoundaryHandler:
    """The runtime of ``(on-raise body pattern target ...)`` -- when called, it returns a handler that maps only a Raise with a matching case to a business answer and ends the scope
    (the macro on-raise expands to ``((on_raise case...) body)``).

    A Raise with no matching case is passed outward. Python exceptions are not received (it looks only at Raise). Success is returned as is.
    """
    if not cases:
        raise TypeError("on-raise needs at least one case")
    stray = [case for case in cases if not isinstance(case, RaiseCase)]
    if stray:
        raise TypeError(
            f"on_raise cases must be RaiseCase: {stray[0]!r} -- on_raise is a handler factory, and the program is applied to the returned handler: "
            "on_raise(cases...)(program) (ADR-DOE-API-001 R6)"
        )

    @do
    def recover_raise(effect: EffectBase, k: object) -> "EffectGenerator[object]":
        if isinstance(effect, Raise):
            for case in cases:
                if isinstance(effect.reason, case.reasons):
                    chosen = case.recover(effect.reason)
                    if isinstance(chosen, Some):
                        return chosen.value
                    if chosen is not Nothing:
                        raise TypeError(f"An on-raise case must return Some or Nothing: {chosen!r}")
        yield Pass(effect, k)

    return _boundary("on_raise", _program_handler(recover_raise), _opened_only)


def absent_as(default: _D, *, token: AbsentAsToken | None = None) -> BoundaryHandler:
    """The runtime of ``(absent-as default body)`` (R8) -- when called, it returns a handler that folds an Absent emitted inside into the default value
    (the macro absent-as expands to ``((absent_as default :token marker) body)``).

    Among the Absents emitted inside, those carrying the marker ``token`` (the ones that a ``<-`` inside absent-as's literal text emitted in place of a value) are
    resumed with the default value, and the others (an Absent deep inside a called defk, or a directly written (<- (Absent ...))) are not resumed and end the scope with the default value.
    Bytecode compiled from the previous macro expansion (``absent_as(default, body, marker)``) is recompiled by doeff-hy's bytecode watcher
    when it sees the macro change.
    """

    @do
    def default_absent(effect: EffectBase, k: object) -> "EffectGenerator[object]":
        if isinstance(effect, Absent):
            if token is not None and isinstance(effect, BoundAbsent) and effect.token is token:
                return (yield Transfer(k, AbsentDefault(token, default)))
            return default
        yield Pass(effect, k)

    return _boundary("absent_as", _program_handler(default_absent), _opened_only)


def absent_raises(reason: Callable[[], object]) -> ProgramHandler:
    """A receiver that turns an Absent emitted inside into ``Raise(reason())`` (``<-``'s ``:absent`` wraps one binding)."""

    @do
    def absent_to_raise(effect: EffectBase, k: object) -> "EffectGenerator[object]":
        if isinstance(effect, Absent):
            return (yield _raise(reason()))
        yield Pass(effect, k)

    return _program_handler(absent_to_raise)


def new_absent_token() -> AbsentAsToken:
    """Make the marker of one evaluation of absent-as (called by the macro absent-as's expansion)."""
    return AbsentAsToken()


def direct_bind(token: AbsentAsToken, expr: object) -> DirectBind:
    """Wrap the expression of one binding inside absent-as's literal text with the marker (called by the macro absent-as's expansion)."""
    return DirectBind(token, expr)

