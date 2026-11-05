"""Type declarations referenced only by the expansion for type checking (doeff_hy/_static_view.py).

The runtime expansion does not import this module; it is shown only to pyright. The types themselves use doeff core's
(`Expand[T, E]`, `Program[T, E]`, `EffectBase[T]` - docs/23-static-typing.md),
and this file only holds the entry points that match the shape of the Hy expansion.

- `do`: wraps functions such as defk / defp. It is core's `doeff.do.do` itself (core also has the overload for functions with no yield in the body -
  a defk without yield becomes a plain function).
- `_doeff_perform(e)`: the type of x in `(<- x e)` = the answer type of e (same shape as Python's `@effectful`
  `x = perform(e)` - docs/24-effectful-perform.md). For an effect, the T of `EffectBase[T]`;
  for a Program (such as the result of calling a defk), the T of `Program[T, E]`. For an effect that declares answer splitting (:absent, :failure), the type with those
  members removed from T (`BoundRun` below); the relay `Perform(e)` does not open the declaration, so it gets the whole answer of e.
  The declaration is only the single `Program[_T, object] -> _T`, with no `object -> Any` catch-all (decided cisco-c8 2026-10-03 16:3x):
  with a catch-all overload, when the first evaluation in a module was under a union expected type (`(<- x (| int None) …)`),
  pyright 1.1.414 dropped every later `_doeff_perform` to the catch-all, the answer became Any, and
  bind type mismatches passed silently (order-dependent - test_a_bind_after_a_union_bind_keeps_its_answer_type in
  tests/test_static_check.py). Passing a non-Program value to `<-` becomes an error.
  Hy has no way yet to declare an effect set, so `perform: Effects[E]` is not matched against E.
- The argument types of deftest (`DeftestInterpreter` and the types of pytest's builtin fixtures; section below).
- The types of the defhandler / handle expansion (the function that runs clauses, the function that wraps a handler, the check of how clauses end, and the trailing clause).
"""

from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, ParamSpec, Protocol, TypeAlias, TypeVar

import pytest

from doeff import K, Program, WithHandlerType

# `do` is passed through from core as is, with no second declaration. A module that uses for/do writes the `(import doeff [do :as _doeff-do])`
# named by the expansion itself, so the user's `_doeff_do` comes after the `_doeff_do` that doeff-hy-check places at the top of the module.
# If the two types differed, pyright took the later one, and read the defk projection (the `<-` in the body becomes `_doeff_perform` with no yield)
# with the one lacking the overload for functions without yield, so the answer of calling a defk became Unknown.
# With a single type, it is the same type whichever import comes later.
from doeff.do import do as do

_T = TypeVar("_T")
_Bound = TypeVar("_Bound")

#: The return value of `__iter__` of an effect that declares answer splitting (:absent, :failure). The expansion of defeffect for type checking
#: (declarations.hy) and the generation of type declarations (static_stub.py) narrow `__iter__` of the declared effect class to this type - the type argument = the :answer
#: union with the :absent and :failure members removed. `Program[T, E]` reads T from the return value of `__iter__`, so `_doeff_perform(e)` returns
#: the type of the `<-` bound name (success and the :value answer) without an additional overload. At runtime `<-` turns absence into Absent and failure into Raise and
#: emits them in the caller's scope, so they never reach the bound name (ADR-DOE-CORE-EFFECTS-003 R5, R6). The Answer of the effect base `EffectBase[Answer]` stays
#: the whole declaration (handlers answer with values in all three cases - R4 - so `typed_resume` accepts all of them). It is not made an overload because, when the annotation does not match,
#: the diagnostic would change from reportAssignmentType to reportCallIssue (No overloads), breaking the matching of users' known errors.
BoundRun: TypeAlias = Generator[Any, Any, _Bound]

def _doeff_perform(effect: Program[_T, object], /) -> _T: ...

# The argument type for items of for/do and traverse. The expansion for type checking (_traverse-form in macros.hy)
# takes the per-item function's argument as `object` and re-reads it before the body as `x = _doeff_traverse_item(items, item)` - the item is
# the element of items itself (the Traverse handler passes each item of items), so the answer is the element type of items.
def traverse_item(items: Iterable[_T], item: object, /) -> _T: ...

# ---------------------------------------------------------------------------
# Argument types for the deftest expansion
# ---------------------------------------------------------------------------
# In the expansion for type checking, annotations are attached to the arguments of the function deftest creates. The annotation names are looked up by DEFTEST_FIXTURE_TYPES
# of doeff_hy/_static_view.py (the table from fixture name to type name here - the single source of truth), and doeff-hy-check imports them at the top of the module
# as `_doeff_<type name>`. For a fixture not in the table (one the user defined in conftest), the writer's `#^ T name` annotation is used if present,
# otherwise `object`.

class DeftestInterpreter(Protocol):
    """The fixture `doeff_interpreter` - a function that runs deftest's Program (defined by the consuming repo's conftest).

    The expansion calls it as `(doeff_interpreter program)`, or `(doeff_interpreter program :env {…})` when `:env` is present.
    The answer is unused by the expansion (the test function returns None), so it is object. Program is covariant in both answer and effect,
    so `Program[object, object]` accepts any Program."""

    def __call__(
        self, program: Program[object, object], /, *, env: Mapping[str, object] = ...
    ) -> object: ...

# Types of pytest's builtin fixtures (pytest's public names).
TmpPath: TypeAlias = Path
TmpPathFactory: TypeAlias = pytest.TempPathFactory
TmpdirFactory: TypeAlias = pytest.TempdirFactory
MonkeyPatch: TypeAlias = pytest.MonkeyPatch
CaptureStr: TypeAlias = pytest.CaptureFixture[str]
CaptureBytes: TypeAlias = pytest.CaptureFixture[bytes]
LogCapture: TypeAlias = pytest.LogCaptureFixture
FixtureRequest: TypeAlias = pytest.FixtureRequest
PytestConfig: TypeAlias = pytest.Config
WarningsRecorder: TypeAlias = pytest.WarningsRecorder
PytestCache: TypeAlias = pytest.Cache
Subtests: TypeAlias = pytest.Subtests
RecordProperty: TypeAlias = Callable[[str, object], None]
DoctestNamespace: TypeAlias = dict[str, object]

# ---------------------------------------------------------------------------
# Types for the defhandler / handle expansion
# ---------------------------------------------------------------------------
# The expansion for type checking (doeff_hy/handle.hy) looks up names from HANDLER_STATIC_NAMES in doeff_hy/_static_view.py
# as `_doeff_<name>`. The runtime expansion attaches no annotations, and the check of how clauses end is imported from clause_endings.hy.

#: The answer type of the body that a handler wraps. The wrapped result carries the same answer type (a handler does not change the body's answer).
HandledAnswer = TypeVar("HandledAnswer")

#: The answer of the function that runs clauses: it emits nodes such as effects, Pass, and Resume / Transfer; the value sent back is the answer of the continuation or
#: the answer of the outer handler, and the clause's answer is the answer of the scope where the handler was placed (finish) - all values of undetermined type, so object.
ClauseRun: TypeAlias = Generator[object, object, object]
#: The argument k (continuation) of the function that runs clauses.
Continuation: TypeAlias = K
#: The body that a handler wraps (accepts any Program or effect - Program is covariant in both answer and effect).
HandlerBody: TypeAlias = Program[HandledAnswer, object]
#: The result of wrapping with a handler (the same answer type as the body).
HandledScope: TypeAlias = WithHandlerType[HandledAnswer]

class Handler(Protocol):
    """The function defhandler creates - wraps the body with a responder (the answer type stays the body's). The generation of type declarations (static_stub.py)
    uses it for the answer type of defhandler."""

    def __call__(self, body: HandlerBody[HandledAnswer], /) -> HandledScope[HandledAnswer]: ...

#: Description of one clause: (the effect's type, the name of the operation used, whether there is a reason for ending) - a tuple made by ending-spec-form in handle.hy.
_ClauseEnding: TypeAlias = tuple[object, tuple[str, ...], bool]

def check_clause_endings(handler: str, clauses: Sequence[_ClauseEnding], /) -> None: ...
def check_clause_endings_once(
    data: Callable[..., object], handler: str, specs: Callable[[], Sequence[_ClauseEnding]], /
) -> None: ...
def fell_through(handler: str, effect_label: str, /) -> RuntimeError: ...

# The type of a clause for a generic effect. The clause's effect is taken as `object`, and the clause's `isinstance` narrows it. Pyright
# makes the type arguments of narrowing to a generic class Unknown (`Race[Unknown]`, `Spawn[Unknown, Unknown]` - even if the narrowed-from type is `EffectBase[Any]`,
# only the type arguments solvable from the base are filled, and type arguments absent from the base, like `_E` of `Spawn`, stay Unknown. The default values of type variables are not used in isinstance
# narrowing), so reads of fields and arguments of `typed_resume` / `typed_transfer` become partly-Unknown errors.
# There is no way to know the number of type arguments at expansion time (a macro cannot look up the class of an imported name), so the type arguments are solved with a constructor call:
# `any_specialized(Race, *any_args, **any_kwargs)` has all constructor arguments as Any, so it becomes the type with all the constructor's type arguments solved as Any
# (`Race[Any]`, `Spawn[Any, Any]`; for a non-generic class, that class). This call is never run (the expansion for type checking is only
# read by pyright), so no behavior to create an effect is needed; only the type is the answer.
_P = ParamSpec("_P")
_R = TypeVar("_R")

def any_specialized(factory: Callable[_P, _R], /, *args: _P.args, **kwargs: _P.kwargs) -> _R: ...

#: The arguments of `any_specialized`'s constructor (both positional and named; Any regardless of count and type).
any_args: tuple[Any, ...]
any_kwargs: dict[str, Any]
