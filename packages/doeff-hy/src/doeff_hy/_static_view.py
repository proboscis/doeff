"""Switch for turning doeff-hy macros into the "expansion for type checking" (single entry point).

`doeff-hy-check` (doeff_hy/_static_check.py) expands Hy source with the real macros and
feeds the resulting Python to pyright. Among the runtime expansions, only the places that carry
no type information needed for type checking (`x = yield e` of `(<- x T e)` cannot derive x's type from e)
are emitted in a type-aware form while this switch is on. The switch is read only at macro
expansion time (not at runtime), so ordinary imports, the bytecode cache, and runtime semantics are unchanged.

Places where the shape changes under the switch (all places in macros.hy that read `_static-view?`):
- `_bind-yield`: `(setv x (yield e))` → `_hy_gensym_bound_N: T = _doeff_perform(e)` then `x = _hy_gensym_bound_N`
  (the declared type is on the temporary, so same-named binds of other types in other clauses / branches do not collide)
  (`_doeff_perform` is a statically declared function that returns the type of e's answer. doeff_hy/_static_types.pyi.
  Same shape as `x = perform(e)` of Python's `@effectful`)
- defk / defp / do! / deftest / fnk: take `do` from the typed `doeff_hy._static_types.do`
  (the call result becomes a Program carrying the answer type T, i.e. core's `Expand[T, E]`)
- `resume` / `transfer` of defhandler: core's `typed_resume` / `typed_transfer`
  (match the answer value against T of the effect's `EffectBase[T]`)
- defhandler / handle functions (handle.hy): the clause-dispatch function's argument `effect: object`
  (the clause's `isinstance` narrows the type), `k: Continuation`, the answer `ClauseRun`; the body of
  the function that wraps the handler `HandlerBody[HandledAnswer]` -> `HandledScope[HandledAnswer]`; the check of how clauses end is taken from the typed
  static_types names (HANDLER_STATIC_NAMES). The doeff nodes import only static-nodes of the clause operation table (clause_ops.hy) and
  the handler's own Pass (Resume / Transfer are replaced by typed_resume / typed_transfer, so they are not used)
- the clause effect of defhandler / handle (`_effect-form-binding` in handle.hy): when a clause binds fields or reads the effect with typed_resume /
  typed_transfer, `_doeff_effect` after narrowing by `isinstance` is rebound to `any_specialized(EffectType, *any_args,
  **any_kwargs)`. pyright makes the narrowed type arguments of a generic class Unknown (`Race[Unknown]`), but a constructor call resolves all
  type arguments to Any (`Race[Any]`), so the field type becomes the type in the effect's definition (`tuple[Task[Any] | Future[Any], ...]`) and no Unknown error appears
- deftest functions: type annotations for fixture arguments (DEFTEST_FIXTURE_TYPES), return value None, and the pytest decorator is
  `_doeff_pytest` (the runtime expansion returns the interpreter's answer without annotations)
- return value of defk: annotate T of `(: % T)` in `:post` (the defk in this expansion is an
  ordinary function with no yield, so even a defk that calls itself again can read its answer type without relying on inference. The runtime defk is a generator so this is not added;
  `_annotates-return?` in macros.hy)
- session val / session var of defhandler (`_build-lazy-init-forms` in handle.hy): names are
  bound to the initial value (Get is not used). Put / Some for rewriting a session var (`:=` and the old `set!`) and session_key for the initialization key are
  taken via `_doeff_<name>` and come from the helper imports at the head of the module (SESSION_STATIC_IMPORTS)
- defmcp-tool: emits no imports at all. The handler's decorator is `_doeff_do` at the head of the module,
  and the tool values McpToolDef / McpParamSchema are taken via `_doeff_<name>` from the helper imports at the head of the module (MCP_STATIC_IMPORTS)

Passing findings (ADR-DOE-HY-006): of the val / var checks, those that do not stop expansion (use of setv, rebinding of the same
name, the old lazy-val / set! of defhandler) are passed by the macro to `report_findings`. They are collected only when doeff-hy-check
expands inside `collect_findings`, and are shown as warnings / errors (discarded in ordinary imports).

Places common to both expansions that have a shape for types (not evaluated at runtime, no change in meaning):
string annotations of arguments and the return value of deff, the result variable `_contract_result: T`, and helper imports
(types are in macros.pyi).
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

# The mark for the type-checking expansion is held by the bytecode wrapper: so that the wrapper's compile entry does not attach macro dependency
# records to the non-executable code produced by this expansion and it does not pass from the .pyc and shared code store into ordinary imports (doeff_hy_bytecode_guard/_expansion.py).
from doeff_hy_bytecode_guard._expansion import TYPE_CHECK_EXPANSION as _STATIC_VIEW

from doeff_hy._binding_forms import Finding
from doeff_hy._expansion_names import alias, import_source


@dataclass(frozen=True)
class FixtureType:
    """One pytest fixture name and the type of its value (a type name in doeff_hy/_static_types.pyi)."""

    fixture: str
    type_name: str


#: Argument types of the test functions deftest produces: the sole source of definition for pairs of fixture name and static_types.pyi type name.
#: In the expansion for type checking, deftest (macros.hy) attaches the annotation `_doeff_<type name>` to arguments via `deftest_fixture_annotation`,
#: and doeff-hy-check imports that name from static_types at the head of the module (STATIC_HELPER_IMPORTS). For names not here
#: (fixtures the user defined in conftest, arguments of :params), the author's `#^ T name` annotation is used as is, or `object` if absent.
#: Among pytest built-ins, tmpdir is not listed because its type (py.path.local) is not under a public pytest name (it becomes object).
DEFTEST_FIXTURE_TYPES: tuple[FixtureType, ...] = (
    FixtureType("doeff_interpreter", "DeftestInterpreter"),
    FixtureType("tmp_path", "TmpPath"),
    FixtureType("tmp_path_factory", "TmpPathFactory"),
    FixtureType("tmpdir_factory", "TmpdirFactory"),
    FixtureType("monkeypatch", "MonkeyPatch"),
    FixtureType("capsys", "CaptureStr"),
    FixtureType("capteesys", "CaptureStr"),
    FixtureType("capfd", "CaptureStr"),
    FixtureType("capsysbinary", "CaptureBytes"),
    FixtureType("capfdbinary", "CaptureBytes"),
    FixtureType("caplog", "LogCapture"),
    FixtureType("request", "FixtureRequest"),
    FixtureType("pytestconfig", "PytestConfig"),
    FixtureType("recwarn", "WarningsRecorder"),
    FixtureType("cache", "PytestCache"),
    FixtureType("subtests", "Subtests"),
    FixtureType("record_property", "RecordProperty"),
    FixtureType("record_xml_attribute", "RecordProperty"),
    FixtureType("record_testsuite_property", "RecordProperty"),
    FixtureType("doctest_namespace", "DoctestNamespace"),
)


def deftest_annotation_name(type_name: str) -> str:
    """Turn a static_types type name into the name used inside the expansion (an alias for the import at the head of the module)."""
    return alias(type_name)


def deftest_fixture_annotation(fixture: str) -> str:
    """The annotation name attached to a deftest argument (mangled name) in the type-checking expansion (names not in the table get `object`)."""
    found = [entry.type_name for entry in DEFTEST_FIXTURE_TYPES if entry.fixture == fixture]
    return deftest_annotation_name(found[0]) if found else "object"


#: Names from static_types referenced by the type-checking expansion of defhandler / handle -- inside the expansion they are
#: taken as `_doeff_<name>`, and doeff-hy-check imports them exactly once at the head of the module (STATIC_HELPER_IMPORTS).
#: - Types: the clause-dispatch function's answer `ClauseRun`, the continuation `Continuation`, the body that wraps the handler `HandlerBody[HandledAnswer]`, and
#:   the wrapped result `HandledScope[HandledAnswer]` (a type variable that carries the body's answer type as is).
#: - Functions: the checks of how clauses end (check-clause-endings, check-clause-endings-once, fell-through in clause_endings.hy).
#:   The implementation is .hy so pyright cannot see the types -- the runtime expansion imports from clause_endings as before.
#: - Clauses for generic effects: `any_specialized`, which resolves all type arguments to Any in a constructor call, and its call arguments `any_args`, `any_kwargs`
#:   (narrowing by a clause's `isinstance` makes the type arguments of a generic class Unknown, so field reads and typed_resume arguments
#:   become partially-Unknown errors. `_effect-form-binding` in handle.hy).
HANDLER_STATIC_NAMES: tuple[str, ...] = (
    "ClauseRun",
    "Continuation",
    "HandledAnswer",
    "HandledScope",
    "HandlerBody",
    "any_args",
    "any_kwargs",
    "any_specialized",
    "check_clause_endings",
    "check_clause_endings_once",
    "fell_through",
)

#: Names from static_types referenced by the type-checking expansion of defeffect (the return value of `__iter__` for an effect that declares answer branches -- declarations.hy).
DEFEFFECT_STATIC_NAMES: tuple[str, ...] = ("BoundRun",)

#: Names from static_types referenced by the type-checking expansion of for/do and traverse -- `traverse_item`, which
#: re-reads the argument of the clause as the element type of items (_traverse-form in macros.hy). Inside the expansion it is taken as `_doeff_<name>`.
TRAVERSE_STATIC_NAMES: tuple[str, ...] = ("traverse_item",)

#: Names referenced by the type-checking expansion of session val / session var of defhandler -- inside the expansion they are taken as `_doeff_<name>`.
#: - `Put` and `Some`: used only for rewriting a session var (`:=` -- `_assign` in binding_forms.py; the old `set!` -- macros.hy).
#: - `session_key`: builds the initialization key of session val / session var (`_build-lazy-init-forms` in handle.hy -- the old lazy is not used).
#: Emitting the imports per handler caused strict "import not used" errors for Put / Some in handler modules with neither := nor set!,
#: and the authors could not fix them (7 files, 14 instances in agora-controllers' daily run 2026-10-11 16:00). They are put in the helper imports at the head of the module and
#: placed only when read. The `_doeff_` prefix on names is so that they do not share a single name with the author's own Put / Some imports -- the runtime
#: expansion takes the same alias (expansion_names.py is the sole source of definition for the alias spelling and the import).
SESSION_STATIC_IMPORTS: str = (
    import_source(("Some", "Put")) + "from doeff_hy._session import session_key as _doeff_session_key\n"
)

#: Names referenced by the type-checking expansion of defmcp-tool -- inside the expansion they are taken as `_doeff_<name>` (defmcp-tool in macros.hy).
#: When `from doeff.mcp import McpToolDef, McpParamSchema` was emitted per tool, it was a doeff module and so was not caught by the stack folding
#: (static_check.without_bookkeeping) either; in a module or class with 65 tools, the declarations of McpToolDef exceeded 64,
#: pyright made the tool values Unknown, and type mistakes on the side using the tools silently passed.
#: The `_doeff_` prefix on names is so that they do not share a single name with the author's own McpToolDef import.
MCP_STATIC_IMPORTS: str = "from doeff.mcp import McpToolDef as _doeff_McpToolDef, McpParamSchema as _doeff_McpParamSchema\n"


#: Imports of helper names that macros reference in the type-checking expansion (once at the head of the module -- doeff-hy-check places them).
#: The runtime expansion emits the same import for each defk / defhandler / `<-`, but doing the same in the static expansion piles up
#: declarations on a single name. pyright stops type inference and makes it Unknown when the declarations of a name exceed 64, so in a module
#: with 65 defks `_doeff_do` became Unknown, and defk calls looked like the :post type rather than a Program
#: (4 places of `(Spawn (defk call))` in local.hy of doeff-cluster). Macros in the static expansion
#: do not emit this import (`_helper-imports`, `_bind-yield`, defmcp-tool in macros.hy; `_do-import` in handle.hy).
STATIC_HELPER_IMPORTS: str = (
    "from doeff_hy._static_types import do as _doeff_do, _doeff_perform"
    + "".join(
        f", {name} as {deftest_annotation_name(name)}"
        for name in (
            *sorted({entry.type_name for entry in DEFTEST_FIXTURE_TYPES}),
            *HANDLER_STATIC_NAMES,
            *DEFEFFECT_STATIC_NAMES,
            *TRAVERSE_STATIC_NAMES,
        )
    )
    + "\n"
    "from doeff_hy.macros import _install_guard_globals, _guard_performed, _guard_statement_value, "
    "_doeff_check_program_return\n"
    # pytest pulled by deftest decorators (@pytest.mark.…) (so that the per-deftest import does not overlap with the author's import)
    "import pytest as _doeff_pytest\n" + SESSION_STATIC_IMPORTS + MCP_STATIC_IMPORTS
)

#: A bare `import <module>` emitted when defeffect places answer branches (:absent, :failure, :value) (defeffect-form in declarations.hy).
#: The cleanup in the type-checking expansion folds it as an import synthesized by a macro, like the import bound with a `_doeff_` alias (the form
#: emitted by the owning package's defining macro -- e.g. defsystem in doeff-cluster), and this is the spelling that moves
#: things inside function / class bodies up to module level. That it matches the expansion's spelling is checked by tests/test_nested_definition_scope.py through the projection of defeffect inside a function.
DEFEFFECT_OUTCOMES_MODULE: str = "doeff_core_effects._outcomes"


def static_view_enabled() -> bool:
    return _STATIC_VIEW.get()


@contextmanager
def static_view() -> Iterator[None]:
    token = _STATIC_VIEW.set(True)
    try:
        yield
    finally:
        _STATIC_VIEW.reset(token)


_FINDINGS: ContextVar[list[Finding] | None] = ContextVar("doeff_hy_static_findings", default=None)


def report_findings(findings: Iterable[Finding]) -> None:
    """Pass a macro's findings to the running doeff-hy-check (discarded when none is collecting -- expansion is not stopped)."""
    sink = _FINDINGS.get()
    if sink is not None:
        sink.extend(findings)


@contextmanager
def collect_findings() -> Iterator[list[Finding]]:
    """Collect the findings of macros expanded inside this (doeff-hy-check uses it per file expansion)."""
    found: list[Finding] = []
    token = _FINDINGS.set(found)
    try:
        yield found
    finally:
        _FINDINGS.reset(token)
