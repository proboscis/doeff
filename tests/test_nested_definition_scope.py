"""Check that definition macros written nested inside a function body (defk, defp, deftest, defmcp-tool, defeffect, deff and
every other def-prefixed macro emitted by doeff_hy.macros, plus defrecord, defwire and defenum from doeff_hy.record)
do not create local names in the enclosing function (every macro is counted via samples; class bodies and the type-check projection are checked too).
Definition macros emitted by their owning packages (defsystem in doeff_cluster.macros)
get the same checks in packages/doeff-cluster/tests/test_nested_defsystem_scope.py (split out on 2026-11-04 because root tests do not
import internal packages).

The runtime expansion of defk, defp and deftest imports helpers such as `_doeff_do`, `_guard_statement_value` and `_doeff_effect_base` at the head
(plus `doeff_hy`, `pytest` and `_doeff_QuotedForms`). Importing them inside a function body turns the names into
locals of the whole function, so the traverse / for/do `_doeff_do`, the body-statement guard `_guard_statement_value` and
the final-value guard `_doeff_effect_base`, all read earlier in the same function, failed with UnboundLocalError (the same bug for fnk and do! is in
tests/test_fnk_do_bang_scope.py). defmcp-tool also imports `_doeff_do`, `McpToolDef` and `McpParamSchema` at the head of its expansion.
defeffect imports `_doeff_effect_base`, `_doeff_dataclass`, `_doeff_ClassVar` and others, deff imports the declaration `doeff_hy`, defrecord imports `doeff_hy` (record_runtime and the head-dict declaration), and defwire additionally imports
`_doeff_ClassVar`.
Expansions in module and class bodies are not wrapped (to keep `__qualname__` and zero-argument `super()`). defhandler is checked by tests/test_handle_scope.py;
here only the shape of the bug reproduction is placed.

It also checks what callers of the wrapping `_scoped-definition` must guarantee (failure cases from design review): a definition inside a function
is evaluated on every call of the outer function, so it must not accumulate records in the module globals; helpers read by a function defined in a class body
must be reachable even when the module has no other definitions (a type-building definition also reads nested type bodies and methods). In the type-check projection, the
imports of the expansion inside function and class bodies must not become local names and must resolve at module level.
"""

import ast
import dataclasses
import symtable
import sys
import types
from collections.abc import Callable
from pathlib import Path

import doeff_hy.macros  # registers extensions (also loads the definition macro names)
import doeff_hy.record  # loads the names defrecord, defwire and defenum
import hy
import pytest
from doeff_core_effects import scheduled, try_handler
from doeff_hy._pytest_items import module_record_texts
from doeff_hy._static_check import Projection, project
from doeff_hy_bytecode_guard._records import MACRO_TABLE
from doeff_traverse import fail_handler, sequential

from doeff import run

_SCOPE_LEAKING_NAMES = frozenset(
    {
        "_doeff_do",
        "_doeff_effect_base",
        "_install_guard_globals",
        "_guard_performed",
        "_guard_statement_value",
        "_doeff_check_program_return",
        "_doeff_QuotedForms",
        "doeff_hy",
        "pytest",
        "McpToolDef",
        "McpParamSchema",
        "_doeff_dataclass",
        "_doeff_ClassVar",
        "_doeff_cast",
        "_doeff_object",
        "doeff_core_effects",
        "WithHandler",
        "Resume",
        "Transfer",
        "Pass",
        "_doeff_check_clause_endings_once",
    }
)

# There is one module-level defk (the usual shape of an author module) -- the helpers the outer function reads first are in the module globals
_HEADER = """
(require doeff-hy.macros [defk defp defpp deftest defmcp-tool defeffect deff defhandler defpipeline
                          <- traverse for/do])
(require doeff-hy.record [defrecord defwire defenum])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(defk _module-level [] {:pre [] :post [(: % int)]} 0)
"""


def _eval_module(code: str, header: str = _HEADER) -> types.ModuleType:
    """Load Hy source into a fresh module -- so module globals are separated per check."""
    mod = types.ModuleType("nested_definition_scope")
    sys.modules["nested_definition_scope"] = mod
    for form in hy.read_many(header + code):
        hy.eval(form, mod.__dict__, module=mod)
    return mod


def _run(program: object) -> object:
    """Run a Program containing nested definitions under the traverse / for/do handler."""
    return run(scheduled(try_handler(fail_handler(sequential(program)))))


def _run_defk(arg: object) -> Callable[[types.ModuleType], object]:
    """Run the Program that calls the outer definition `subject` with arg -- to actually exercise a function containing nested definitions."""
    return lambda mod: _run(mod.subject(arg))


def _collection_values(mod: types.ModuleType) -> object:
    """Run the Collection of the plain defn `subject` that returns a for/do and turn it into a sequence of values -- to compare for/do results."""
    return _run(mod.subject([1, 2, 3])).valid_values


_CASES = {
    # The import of the nested defk comes after the outer defk reads the statement guard (_guard_statement_value)
    "defk-in-defk-after-statement": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % int)]}
          (abs x)
          (defk inner [y] {:pre [(: y int)] :post [(: % int)]} (+ y 1))
          (<- r (inner x))
          r)
        """,
        _run_defk(5),
        6,
    ),
    # The import of the nested defk comes after the outer defk reads traverse `_doeff_do`
    "defk-in-defk-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (defk inner [y] {:pre [(: y int)] :post [(: % int)]} (+ y 1))
          (<- r (inner (len items)))
          r)
        """,
        _run_defk([1, 2, 3]),
        4,
    ),
    # After the nested defk in a branch that does not run, the outer final-value guard (_doeff_effect_base) is read
    "defk-in-untaken-branch": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % int)]}
          (when (> x 100) (defk inner [y] {:pre [(: y int)] :post [(: % int)]} y))
          x)
        """,
        _run_defk(5),
        5,
    ),
    "defp-in-defk-after-statement": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % int)]}
          (abs x)
          (defp inner {:post [(: % int)]} (+ x 1))
          (<- r inner)
          r)
        """,
        _run_defk(5),
        6,
    ),
    # The import of the nested defk comes after the plain defn reads for/do `_doeff_do`
    "defk-in-defn-after-for-do": (
        """
        (defn subject [items]
          (setv p (for/do (<- x (From items)) (* x 2)))
          (defk inner [y] {:pre [(: y int)] :post [(: % int)]} y)
          p)
        """,
        _collection_values,
        [2, 4, 6],
    ),
    # Local helper defk inside a check: a nested defk after the body-statement guard of deftest
    "defk-in-deftest-after-statement": (
        """
        (deftest test-subject
          (abs 1)
          (defk inner [y] {:pre [(: y int)] :post [(: % int)]} (+ y 1))
          (<- r (inner 1))
          (assert (= r 2)))
        """,
        lambda mod: mod.test_subject(_run),
        None,
    ),
    "deftest-in-defk-after-statement": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % int)]}
          (abs x)
          (deftest test-inner (assert True))
          x)
        """,
        _run_defk(5),
        5,
    ),
    # Function that builds a check: a nested deftest after the for/do of a plain defn (import pytest must not become a local of the outer function either)
    "deftest-in-defn-after-for-do": (
        """
        (defn subject [items]
          (setv p (for/do (<- x (From items)) (* x 2)))
          (deftest test-inner {:marks ["slow"]} (assert True))
          p)
        """,
        _collection_values,
        [2, 4, 6],
    ),
    # The import of the nested defmcp-tool comes after the plain defn reads for/do `_doeff_do` (steps to reproduce the bug)
    "defmcp-tool-in-defn-after-for-do": (
        """
        (defn subject [items]
          (setv p (for/do (<- x (From items)) (* x 2)))
          (defmcp-tool echo "Echo" [{:name "text" :type "string" :description "text"}] text)
          p)
        """,
        _collection_values,
        [2, 4, 6],
    ),
    # A nested defmcp-tool after the outer defk traverse -- the tool handler can be called from the outer function
    "defmcp-tool-in-defk-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (defmcp-tool inc "Inc" [{:name "y" :type "integer" :description "y"}] (+ y 1))
          (<- r ((. inc handler) (len items)))
          r)
        """,
        _run_defk([1, 2, 3]),
        4,
    ),
    # The import of the nested defhandler comes after the plain defn reads for/do `_doeff_do` (steps to reproduce the bug)
    "defhandler-in-defn-after-for-do": (
        """
        (defn subject [items]
          (setv p (for/do (<- x (From items)) (* x 2)))
          (defhandler inner (Ask [k] (resume 1)))
          p)
        """,
        _collection_values,
        [2, 4, 6],
    ),
    # After the nested defeffect in a branch that does not run, the outer final-value guard (_doeff_effect_base) is read (steps to reproduce the bug)
    "defeffect-in-untaken-branch": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % int)]}
          (abs x)
          (when (> x 100)
            (defeffect Inner "Inner." {:fields [(: x int)] :answer int :tags {:context "t" :role "intent"}}))
          x)
        """,
        _run_defk(5),
        5,
    ),
    "defeffect-in-defn-after-for-do": (
        """
        (defn subject [items]
          (setv p (for/do (<- x (From items)) (* x 2)))
          (defeffect Inner {:fields [(: x int)] :answer int :tags {:context "t" :role "intent"}})
          p)
        """,
        _collection_values,
        [2, 4, 6],
    ),
    # The outer defk builds the wrapped defeffect type -- :pre (__post_init__), how answers are split (import of doeff_core_effects), and the freezing finish
    "defeffect-in-defk-builds-instance": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % int)]}
          (abs x)
          (defeffect Inner {:fields [(: n int)] :pre [(> n 0)] :answer (| int str) :absent [str]
                            :tags {:context "t" :role "intent"}})
          (+ (. (Inner x) n) (len (. Inner __doeff_outcomes__ absent))))
        """,
        _run_defk(5),
        6,
    ),
    # The import of the nested deff declaration (doeff_hy) comes after the outer function reads module-level doeff_hy
    "deff-after-module-doeff-hy-read": (
        """
        (defn subject [x]
          (setv owner (. doeff_hy __name__))
          (deff inner [y] {:pre [(: y int)] :post [(: % int)]} (+ y 1))
          #(owner (inner x)))
        """,
        lambda mod: mod.subject(5),
        ("doeff_hy", 6),
    ),
    # The import of record_runtime (doeff_hy) of the nested defrecord comes after the outer function reads module-level doeff_hy
    # (steps to reproduce the bug)
    "defrecord-after-module-doeff-hy-read": (
        """
        (defn subject [] (setv owner (. doeff_hy __name__)) (defrecord Span #^ int start) owner)
        """,
        lambda mod: mod.subject(),
        "doeff_hy",
    ),
    # defwire of the same shape (steps to reproduce the bug -- the imports of `_doeff_ClassVar` and doeff_hy._wire come)
    "defwire-after-module-doeff-hy-read": (
        """
        (defn subject [] (setv owner (. doeff_hy __name__)) (defwire Span {:names :camel} #^ int start) owner)
        """,
        lambda mod: mod.subject(),
        "doeff_hy",
    ),
    # Place a defrecord with a head dict after the statement guard of the outer defk and build the type -- the __post_init__ of :check reads the expansion import
    # doeff_hy (require-check), and a value that fails the check raises ValueError
    "defrecord-in-defk-checks-fields": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % tuple)]}
          (abs x)
          (defrecord Span {:tags {:context "t" :role "type"} :check [(>= start 0)]} #^ int start)
          (setv refused (try (Span :start (- x)) (except [ValueError] True)))
          #((. (Span :start x) start) refused (. Span __doeff_tags__ role)))
        """,
        _run_defk(5),
        (5, True, "type"),
    ),
    # A nested defwire after the outer defk traverse -- the resolver (parse-json) resolves that type by its wire name
    "defwire-in-defk-after-traverse-parses": (
        """
        (import doeff_hy._wire [parse-json])
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (defwire Span {:names :camel :check [(>= start-at 0)]} #^ int start-at)
          (<- row (parse-json Span (.format "{{\\"startAt\\": {}}}" (len items))))
          (. row start-at))
        """,
        _run_defk([1, 2, 3]),
        3,
    ),
    # A defk in a class body inside a function stays as the class-level expansion (methods read helpers from the module globals)
    "defk-in-class-in-defn": (
        """
        (defn subject [x]
          (defclass C []
            (defk m [self y] {:pre [(: self C) (: y int)] :post [(: % int)]} (abs y) (+ y 1)))
          ((. (C) m) x))
        """,
        _run_defk(5),
        6,
    ),
}


@pytest.mark.parametrize(("source", "call", "expected"), _CASES.values(), ids=_CASES.keys())
def test_nested_definition_runs(
    source: str, call: Callable[[types.ModuleType], object], expected: object
) -> None:
    assert call(_eval_module(source)) == expected


@pytest.mark.parametrize(
    ("source", "call", "expected"),
    [case for name, case in _CASES.items() if not name.startswith("defk-in-deftest")],
    ids=[name for name in _CASES if not name.startswith("defk-in-deftest")],
)
def test_outer_function_binds_no_helper_name(
    source: str, call: Callable[[types.ModuleType], object], expected: object
) -> None:
    """Names of nested-definition helpers do not appear among the outer function locals (or closure cells) -- if they did, earlier reads would become unbound."""
    subject = _eval_module(source).subject
    code = getattr(subject, "__doeff_generator_function__", subject).__code__
    assert _SCOPE_LEAKING_NAMES.isdisjoint(code.co_varnames + code.co_cellvars)


# For each definition macro emitted by doeff_hy.macros (macros whose name starts with def): the sample placed inside a function body, and the names
# that sample may bind to the outer function (the defined names). When adding a macro, add a sample -- otherwise test_every_definition_macro_has_a_nested_sample goes red,
# and the added sample makes test_function_with_only_a_definition_binds_only_its_names check helper-name leaks regardless of name
# (the same leak had been found later for each definition macro).
_DEFINITION_SAMPLES: dict[str, tuple[str, frozenset[str]]] = {
    "defk": ("(defk inner [y] {:pre [(: y int)] :post [(: % int)]} y)", frozenset({"inner"})),
    "deff": ("(deff inner [y] {:pre [(: y int)] :post [(: % int)]} y)", frozenset({"inner"})),
    "defp": ("(defp inner {:post [(: % int)]} 1)", frozenset({"inner"})),
    "defpp": ("(defpp inner {:post [(: % int)]} (Pure 1))", frozenset({"inner"})),
    "deftest": ('(deftest test-inner {:marks ["slow"]} (assert True))', frozenset({"test_inner"})),
    "defeffect": (
        "(defeffect Inner {:fields [(: x int)] :pre [(> x 0)] :answer (| int str) :absent [str] "
        ':tags {:context "t" :role "intent"}})',
        frozenset({"Inner"}),
    ),
    "defmcp-tool": (
        '(defmcp-tool echo "Echo" [{:name "text" :type "string" :description "text"}] text)',
        frozenset({"echo"}),
    ),
    "defhandler": ("(defhandler inner (Ask [k] (resume 1)))", frozenset({"inner"})),
    "defpipeline": (
        "(defpipeline daily [a] (+ 1 2) [b] (* a 2))",
        frozenset({"p_daily_a", "p_daily_b", "p_daily"}),
    ),
    "defrecord": (
        '(defrecord Span {:tags {:context "t" :role "type"} :check [(>= start 0)]} #^ int start)',
        frozenset({"Span"}),
    ),
    "defwire": (
        "(defwire Span {:names :camel :check [(>= start-at 0)]} #^ int start-at)",
        frozenset({"Span"}),
    ),
    "defenum": ("(defenum Kind ALPHA BETA)", frozenset({"Kind"})),
}


def _exported_definition_macros() -> set[str]:
    """Names of definition macros an author can pull with `(require doeff-hy.macros [...])` or `(require doeff-hy.record [...])` (including defhandler,
    re-exported from handle.hy)."""
    named = {
        hy.unmangle(name)
        for module in (doeff_hy.macros, doeff_hy.record)
        for name in vars(module)[MACRO_TABLE]
    }
    return {name for name in named if name.startswith("def")}


def test_every_definition_macro_has_a_nested_sample() -> None:
    """Definition macros emitted by doeff_hy.macros and doeff_hy.record need a sample placed inside a function body -- for a macro without a sample,
    nobody checks whether its helper imports become local names of the outer function (an added macro stays red until its sample is added)."""
    assert _exported_definition_macros() == set(_DEFINITION_SAMPLES)


@pytest.mark.parametrize(
    ("source", "defined"), _DEFINITION_SAMPLES.values(), ids=_DEFINITION_SAMPLES.keys()
)
def test_function_with_only_a_definition_binds_only_its_names(
    source: str, defined: frozenset[str]
) -> None:
    """The locals (and closure cells) of a function containing only one definition are limited to the defined names and the unique names Hy creates (prefixed `_hy_` -- names of
    functions inside the expansion). It does not depend on the helper-name table (_SCOPE_LEAKING_NAMES), so it catches a leak even
    when a macro imports a new helper."""
    subject = _eval_module(f"(defn subject [] {source} None)").subject
    names = set(subject.__code__.co_varnames + subject.__code__.co_cellvars)
    assert {name for name in names if not name.startswith("_hy_")} == defined


def _module_globals_view(mod: types.ModuleType) -> dict[str, tuple[int, int | None]]:
    """Per module-global name, (value id, element count if a container) -- to compare whether calling the outer function added names or records to globals."""
    return {
        name: (id(value), len(value) if isinstance(value, list | dict | set | tuple) else None)
        for name, value in vars(mod).items()
    }


@pytest.mark.parametrize(("source", "call", "expected"), _CASES.values(), ids=_CASES.keys())
def test_nested_definition_leaves_module_globals_unchanged(
    source: str, call: Callable[[types.ModuleType], object], expected: object
) -> None:
    """A definition inside a function adds no names or records to the module globals however many times the outer function is called -- the wrapped statements are
    evaluated on every call, so a statement that accumulates records would accumulate once per call (what callers of `_scoped-definition` must guarantee). A deftest
    inside a function does not accumulate the pytest item record (`__doeff_pytest_items__`)."""
    mod = _eval_module(source)
    before = _module_globals_view(mod)
    call(mod)
    call(mod)
    assert _module_globals_view(mod) == before


def _member_ids(value: object) -> tuple[int, ...] | None:
    """Sequence of ids of container elements (keys and values for a dict) -- to tell apart registrations that add or replace contents without replacing the container."""
    match value:
        case dict():
            return tuple(id(part) for item in value.items() for part in item)
        case list() | tuple() | set() | frozenset():
            return tuple(id(member) for member in value)
        case _:
            return None


def _module_state_view(
    mod: types.ModuleType,
) -> dict[tuple[str, str], tuple[int, tuple[int, ...] | None]]:
    """Per (module, name) of the globals of the module that defined the definitions and of loaded doeff modules (names starting with doeff):
    (value id, ids of container elements) -- to compare whether any module gained names, records or registrations on each call of the outer function
    (looking only at the defining module would miss registrations into the index of a doeff module -- blind review B of design verification)."""
    modules = [mod] + [
        module
        for name, module in sorted(sys.modules.items())
        if name.split(".")[0].startswith("doeff") and isinstance(module, types.ModuleType)
    ]
    return {
        (module.__name__, name): (id(value), _member_ids(value))
        for module in modules
        for name, value in list(vars(module).items())
    }


@pytest.mark.parametrize(
    ("source", "defined"), _DEFINITION_SAMPLES.values(), ids=_DEFINITION_SAMPLES.keys()
)
def test_definition_sample_leaves_module_state_unchanged(
    source: str, defined: frozenset[str]
) -> None:
    """When a sample of a definition macro is written inside a function, however many times the outer function is called, it adds no names, records or registrations to the defining module or to doeff modules -- the wrapped statements
    are evaluated on every call (what callers of `_scoped-definition` must guarantee).
    Statements to be announced to the module only once are passed to the module-forms of `_scoped-definition` and are not emitted inside the function. Since it is counted by samples,
    adding a definition macro subjects this check to it together with the sample (it does not rely on a hand-listed _CASES -- blind review A). The first call
    loads helper modules and may add names to the parent package, so compare before and after the second and third calls."""
    mod = _eval_module(f"(defn subject [] {source} None)")
    mod.subject()
    before = _module_state_view(mod)
    mod.subject()
    mod.subject()
    after = _module_state_view(mod)
    assert {
        key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
    } == set()


def test_static_projection_of_nested_deftest_has_no_item_record(tmp_path: Path) -> None:
    """In the projection for type checking (the Python doeff-hy-check feeds to pyright), a deftest inside a function does not expose to the outer function the statement recording the pytest item and
    the local name `doeff_hy` that its import would create -- the runtime expansion does not emit it, so the projection does the same
    (blind review A of design verification. Module-level records are removed by the projection as bookkeeping)."""
    source = tmp_path / "nested_static.hy"
    source.write_text(
        "(require doeff-hy.macros [deftest])\n(defn subject [] (deftest test-inner (assert True)) None)\n",
        encoding="utf-8",
    )
    projection = project(tmp_path, [tmp_path], source)
    assert isinstance(projection, Projection)
    subject = next(
        node
        for node in ast.parse(projection.text).body
        if isinstance(node, ast.FunctionDef) and node.name == "subject"
    )
    imported = {
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(subject)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert "record_at_import" not in ast.unparse(subject)
    assert "doeff_hy" not in imported


@pytest.mark.parametrize(
    ("source", "call", "expected"),
    [
        (
            """
            (defclass Tools []
              (defmcp-tool echo "Echo" [{:name "text" :type "string" :description "text"}] (abs 1) text))
            """,
            lambda mod: _run(mod.Tools.echo.handler("hi")),
            "hi",
        ),
        (
            """
            (defclass Tools []
              (defk twice [self y] {:pre [(: self Tools) (: y int)] :post [(: % int)]} (abs y) (* y 2)))
            """,
            lambda mod: _run(mod.Tools().twice(2)),
            4,
        ),
    ],
    ids=["defmcp-tool", "defk"],
)
def test_class_body_definition_runs_without_module_definitions(
    source: str, call: Callable[[types.ModuleType], object], expected: object
) -> None:
    """A function defined in a class body runs even when the module has no other definitions -- statements are not wrapped in a class body, and imports
    become class attributes that are invisible from functions defined there. Helpers a function reads by name are delivered to the function globals.
    Read it in a module with exactly one definition, so that other definitions (module level, `_install-guard-globals` of a defk in the same class)
    do not put helpers into the module globals and hide the problem."""
    assert call(_eval_module(source, header="(require doeff-hy.macros [defk defmcp-tool <-])")) == expected


# Helpers that the body and methods of the defined type read by name (doeff_hy, _doeff_ClassVar and others) are not reached by importing in the class body
# (the body and methods of a nested class skip the enclosing class level when looking up names). The author module has no
# other definitions that import `doeff_hy` -- only the dataclass import (the default type of defrecord and defwire reads the dataclass in the author scope).
_TYPE_ONLY_HEADER = """
(require doeff-hy.macros [defeffect])
(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass])
"""


def _refused(build: Callable[[], object], error: type[Exception]) -> bool:
    """Whether build() is refused with an error -- to see that the checks at type construction (:check, :pre) work."""
    try:
        build()
    except error:
        return True
    return False


_CLASS_BODY_TYPE_CASES = {
    # :check reads the expansion import doeff_hy (require-check) from a method, and a value that fails the check raises ValueError
    "defrecord-with-check": (
        "(defclass Outer [] (defrecord Span {:check [(>= start 0)]} #^ int start))",
        lambda mod: (
            repr(mod.Outer.Span(start=2)),
            _refused(lambda: mod.Outer.Span(start=-1), ValueError),
        ),
        ("Outer.Span(start=2)", True),
    ),
    # The nested type body reads the __repr__ decorator (doeff_hy._record_runtime) that the expansion writes
    "defrecord-plain": (
        "(defclass Outer [] (defrecord Span #^ int start))",
        lambda mod: repr(mod.Outer.Span(start=3)),
        "Outer.Span(start=3)",
    ),
    "defwire": (
        "(defclass Outer [] (defwire Span {:names :camel :check [(>= start-at 0)]} #^ int start-at))",
        lambda mod: (
            repr(mod.Outer.Span(start_at=2)),
            _refused(lambda: mod.Outer.Span(start_at=-1), ValueError),
        ),
        ("Outer.Span(start_at=2)", True),
    ),
    # The type body reads the annotation `ClassVar[object]` (_doeff_ClassVar) -- if it cannot, the dataclass counts __doeff_answer__ as a field
    "defeffect": (
        "(defclass Outer [] (defeffect Inner {:fields [(: x int)] :pre [(> x 0)] :answer (| int str) :absent [str] "
        ':tags {:context "t" :role "intent"}}))',
        lambda mod: (
            repr(mod.Outer.Inner(x=1)),
            _refused(lambda: mod.Outer.Inner(x=-1), AssertionError),
            mod.Outer.Inner.__doeff_outcomes__.absent,
        ),
        ("Outer.Inner(x=1)", True, (str,)),
    ),
}


@pytest.mark.parametrize(
    ("source", "call", "expected"),
    _CLASS_BODY_TYPE_CASES.values(),
    ids=_CLASS_BODY_TYPE_CASES.keys(),
)
def test_class_body_type_definition_runs_without_module_doeff_hy(
    source: str, call: Callable[[types.ModuleType], object], expected: object
) -> None:
    """Definitions that build a type written directly in a class body (defrecord, defwire, defeffect) are built and usable even if the author module has no `doeff_hy`.
    A name imported in a class body becomes an attribute of the enclosing class, and the body and
    methods of the nested type skip the enclosing class level when looking up names -- if the expansion import is placed only where it is written, it works only in a module that has `doeff_hy` at module level
    (the usual shape of an author module, where other definitions import it). The type `__qualname__` is not wrapped, so it stays `Outer.<name>`."""
    assert "doeff_hy" not in vars(_eval_module("", header=_TYPE_ONLY_HEADER))
    mod = _eval_module(source, header=_TYPE_ONLY_HEADER)
    assert call(mod) == expected
    # The helper function for the statement placed in the module globals is not left as a class attribute after the call
    assert not [name for name in vars(mod.Outer) if "publish" in name]


def _refusal(act: Callable[[], object]) -> str:
    """Name of the error type by which act is refused ("accepted" if not refused) -- to compare whether the frozen __setattr__ / __delattr__ fail with
    NameError because they cannot look up the defined type, or refuse with FrozenInstanceError."""
    try:
        act()
    except (NameError, dataclasses.FrozenInstanceError) as error:
        return type(error).__name__
    return "accepted"


# Where the methods written by the expansion (frozen __setattr__ / __delattr__, default-factory in __init__, and fields with init disabled) read the defined type.
# The type name is an attribute of the enclosing class, so methods cannot look it up by name (name lookup skips the class level).
_CLASS_BODY_TYPE_METHOD_CASES = {
    "defrecord-frozen": (
        "(defclass Outer [] (defrecord Span #^ int start))",
        lambda mod: (
            _refusal(lambda: setattr(mod.Outer.Span(start=2), "start", 9)),
            _refusal(lambda: setattr(mod.Outer.Span(start=2), "other", 9)),
            _refusal(lambda: delattr(mod.Outer.Span(start=2), "start")),
        ),
        ("FrozenInstanceError", "FrozenInstanceError", "FrozenInstanceError"),
    ),
    # An inheriting type can set attributes that are not fields (the frozen __setattr__ passes to the base of the defined type -- the super lookup)
    "defrecord-subclass": (
        "(defclass Outer [] (defrecord Span #^ int start))\n(defclass Sub [Outer.Span])",
        lambda mod: (
            _refusal(lambda: setattr(mod.Sub(start=2), "start", 9)),
            _refusal(lambda: setattr(mod.Sub(start=2), "other", 9)),
        ),
        ("FrozenInstanceError", "accepted"),
    ),
    "defrecord-factory-and-no-init": (
        "(import dataclasses [field])\n"
        "(defclass Outer [] (defrecord Bag #^ int n (setv #^ list xs (field :default-factory list)"
        " #^ int k (field :default 7 :init False))))",
        lambda mod: repr(mod.Outer.Bag(n=1)),
        "Outer.Bag(n=1, xs=[], k=7)",
    ),
    "defwire-frozen": (
        "(defclass Outer [] (defwire Span {:names :camel} #^ int start-at))",
        lambda mod: _refusal(lambda: setattr(mod.Outer.Span(start_at=2), "start_at", 9)),
        "FrozenInstanceError",
    ),
    "defeffect-frozen": (
        '(defclass Outer [] (defeffect Inner {:fields [(: x int)] :answer int :tags {:context "t" :role "intent"}}))',
        lambda mod: (
            _refusal(lambda: setattr(mod.Outer.Inner(x=1), "x", 9)),
            _refusal(lambda: delattr(mod.Outer.Inner(x=1), "x")),
        ),
        ("FrozenInstanceError", "FrozenInstanceError"),
    ),
}


@pytest.mark.parametrize(
    ("source", "call", "expected"),
    _CLASS_BODY_TYPE_METHOD_CASES.values(),
    ids=_CLASS_BODY_TYPE_METHOD_CASES.keys(),
)
def test_class_body_type_methods_read_their_own_class(
    source: str, call: Callable[[types.ModuleType], object], expected: object
) -> None:
    """Methods written by the expansion for defrecord, defwire and defeffect written directly in a class body read the defined type via the `__class__`
    closure, not by name -- the type name is an attribute of the enclosing class and cannot be looked up from methods, so the frozen __setattr__ failed with
    NameError instead of FrozenInstanceError, and types with default-factory or fields with init disabled failed with NameError at construction."""
    assert call(_eval_module(source, header=_TYPE_ONLY_HEADER)) == expected


# Header used by the type-check projection checks -- requires all definition macros and has the author module import `doeff_hy` (the outer function
# reads module-level `doeff_hy`).
_PROJECTION_HEADER = """
(require doeff-hy.macros [defk deff defp defpp deftest defmcp-tool defeffect defhandler defpipeline
                          <- traverse for/do])
(require doeff-hy.record [defrecord defwire defenum])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff_hy)
"""

# The prefix of the aliases the macro attaches to expansion imports (expansion_names.PREFIX), and the root module name -- if a nested scope
# reads these names and they are not bound at module level, pyright reads them as undefined (only module-level names can be looked up from nested scopes).
_PROJECTION_MACRO_NAME_PREFIXES = ("_doeff_",)
_PROJECTION_ROOT_NAMES = frozenset({"doeff_hy", "doeff_core_effects"})


def _projection_of(tmp_path: Path, body: str) -> ast.Module:
    """AST of the type-check projection with body written in an author module (which imports `doeff_hy` and requires the definition macros)."""
    source = tmp_path / "nested_static.hy"
    source.write_text(_PROJECTION_HEADER + body + "\n", encoding="utf-8")
    projection = project(tmp_path, [tmp_path], source)
    assert isinstance(projection, Projection)
    return ast.parse(projection.text)


def _bound_by_imports(scope: ast.AST) -> set[str]:
    """Names bound by imports inside the scope (including nested functions and class bodies) (`import a.b` binds `a`; with `as`, that name)."""
    return {
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(scope)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }


def _module_level_names(tree: ast.Module) -> set[str]:
    """Names bound at module level (imports, assignments, definitions)."""
    table = symtable.symtable(ast.unparse(tree), "projection", "exec")
    return {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_imported() or symbol.is_assigned() or symbol.is_namespace()
    }


def _macro_names_read_from_module(tree: ast.Module) -> set[str]:
    """Macro aliases and root module names that nested scopes (function and class bodies and the functions and classes within) read as module-level names.
    Name lookup follows Python rules (symtable) -- names in a class body cannot be looked up from nested scopes, while function locals can be looked up as closures."""
    found: set[str] = set()

    def walk(table: symtable.SymbolTable) -> None:
        for symbol in table.get_symbols():
            name = symbol.get_name()
            macro_name = (
                name.startswith(_PROJECTION_MACRO_NAME_PREFIXES) or name in _PROJECTION_ROOT_NAMES
            )
            if macro_name and symbol.is_referenced() and symbol.is_global():
                found.add(name)
        for child in table.get_children():
            walk(child)

    walk(symtable.symtable(ast.unparse(tree), "projection", "exec"))
    return found


_STATIC_SCOPES_SOURCE = "(defn subject [] {source} None)\n(defclass Outer [] {source})"


# In the type-check projection, the import names that the expansion leaves inside function and class bodies (per definition macro). The remaining names are read only directly in that definition body and the class body,
# and not by nested scopes (nested type bodies, methods, clause functions) -- the names nested scopes read are checked by the test below for module-level binding.
# When a new helper import remains in a scope, before adding it to this table confirm that nested scopes do not read it (if they do, move it to module level).
_IMPORTS_LEFT_IN_SCOPE = {
    "defeffect": frozenset({"_doeff_dataclass", "_doeff_effect_base"}),
}


@pytest.mark.parametrize("macro", _DEFINITION_SAMPLES.keys())
def test_static_projection_of_nested_definition_leaves_only_known_imports_in_its_scope(
    tmp_path: Path, macro: str
) -> None:
    """In the type-check projection (the Python doeff-hy-check feeds to pyright), the imports that the expansion of definitions written in function and class bodies leaves in the scope
    are only the names in the table above -- regardless of name (even if a macro imports a new helper under another spelling, it is red unless in the table). An import inside a function is
    a local of the whole outer function, and when the outer function reads module-level `doeff_hy` first, pyright reads it as unbound; an import in a class body cannot be looked up
    from nested type bodies and methods (failure cases from blind reviews A and B of design verification).
    Imports from macro composition are moved to module level by the projection post-process `static_check.with_module_level_imports`."""
    source, _ = _DEFINITION_SAMPLES[macro]
    tree = _projection_of(tmp_path, _STATIC_SCOPES_SOURCE.format(source=source))
    allowed = _IMPORTS_LEFT_IN_SCOPE.get(macro, frozenset())
    scopes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.ClassDef) and node.name in {"subject", "Outer"}
    }
    assert {name: _bound_by_imports(scope) - allowed for name, scope in scopes.items()} == {
        "subject": set(),
        "Outer": set(),
    }


@pytest.mark.parametrize(
    ("source", "defined"), _DEFINITION_SAMPLES.values(), ids=_DEFINITION_SAMPLES.keys()
)
def test_static_projection_nested_scopes_read_macro_names_bound_at_module_level(
    tmp_path: Path, source: str, defined: frozenset[str]
) -> None:
    """The macro aliases (`_doeff_ClassVar` and others) and root module names that the nested scopes of the projection (defined type bodies, methods, annotations) read as module-level names
    are all bound at module level -- if not bound, pyright reads them as undefined (failure case from blind review A:
    `from typing import ClassVar as _doeff_ClassVar` of a definition in a class body remained in the outer class, and the nested `Span` could not be read).
    Definitions inside functions are checked with the same samples (a function local can be read as a closure, but confirm it reads the same after being moved to module level)."""
    tree = _projection_of(tmp_path, _STATIC_SCOPES_SOURCE.format(source=source))
    assert _macro_names_read_from_module(tree) <= _module_level_names(tree)


# The doeff nodes the handler expansion reads under `_doeff_<name>` aliases (doeff_hy/_expansion_names.py) -- read by clause functions and the function wrapping the body.
_HANDLER_NODE_ALIASES = frozenset({"_doeff_Pass", "_doeff_WithHandlerType"})


@pytest.mark.parametrize(
    "body",
    [
        "(defn subject [] (defhandler h (Ping [x] (resume x))) h)",
        "(defclass Outer [] (defhandler h (Ping [x] (resume x))))",
    ],
    ids=["function", "class-body"],
)
def test_static_projection_of_nested_defhandler_reads_module_level_nodes(
    tmp_path: Path, body: str
) -> None:
    """In the type-check projection, the imports of `_doeff_Pass` and `_doeff_WithHandlerType` read by a defhandler inside a function or class body are placed at module level
    -- if left in a class body, clause functions and the function wrapping the body look names up from module level, so pyright read
    `"_doeff_Pass" is not defined`. If left inside a function, they become locals of the outer function (the same shape as the projection item of 49a293f19)."""
    tree = _projection_of(
        tmp_path,
        "(require doeff-hy.handle [defhandler])\n"
        '(defeffect Ping {:fields [(: x int)] :answer int :tags {:context "t" :role "intent"}})\n'
        + body,
    )
    scope = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.ClassDef) and node.name in {"subject", "Outer"}
    )
    assert _HANDLER_NODE_ALIASES.isdisjoint(_bound_by_imports(scope))
    assert _HANDLER_NODE_ALIASES <= _module_level_names(tree)


# The typing aliases (doeff_hy/_expansion_names.py) that type-building definitions read in the type body (ClassVar for annotations, :check, and the cast of the answer type).
_TYPING_ALIASES = frozenset({"_doeff_ClassVar", "_doeff_cast"})


def _names_read(scope: ast.AST) -> set[str]:
    """Names read within the scope."""
    return {node.id for node in ast.walk(scope) if isinstance(node, ast.Name)}


# Samples of type-building definitions (reading typing aliases -- defrecord with a head dict (:check), defwire, defeffect)
_TYPE_BUILDING_SAMPLES = {
    name: _DEFINITION_SAMPLES[name][0] for name in ("defrecord", "defwire", "defeffect")
}


@pytest.mark.parametrize(
    "definition", _TYPE_BUILDING_SAMPLES.values(), ids=_TYPE_BUILDING_SAMPLES.keys()
)
def test_static_projection_of_class_body_type_reads_module_level_typing_aliases(
    tmp_path: Path, definition: str
) -> None:
    """In the type-check projection, `_doeff_ClassVar` and `_doeff_cast` read by the type bodies of defeffect, defwire and defrecord (:check) written directly in a class body
    are bound by module-level imports -- if left in a class body, nested type bodies and methods look names up from module level,
    so doeff-hy-check emitted `"_doeff_ClassVar" is not defined` and `"_doeff_cast" is not defined`
    (what remained of the fix in 8a759a421, found by blind reviews A and B of design verification)."""
    tree = _projection_of(tmp_path, f"(defclass Outer [] {definition})")
    outer = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Outer"
    )
    read = _TYPING_ALIASES & _names_read(outer)
    assert read.isdisjoint(_bound_by_imports(outer))
    assert read <= _module_level_names(tree)


def test_module_and_class_level_definitions_are_not_wrapped() -> None:
    """Definitions in module and class bodies are not wrapped -- wrapping would change `__qualname__` (pickle name lookup) and zero-argument super()."""
    mod = _eval_module(
        """
        (defk subject [x] {:pre [(: x int)] :post [(: % int)]} x)
        (defp program {:post [(: % int)]} 1)
        (deftest test-top (assert True))
        (defmcp-tool top-tool "Top" [{:name "y" :type "integer" :description "y"}] y)
        (defeffect TopEffect {:fields [(: n int)] :answer int :tags {:context "t" :role "intent"}})
        (deff top-helper [y] {:pre [(: y int)] :post [(: % int)]} y)
        (defclass Base [] (defn m [self y] y))
        (defclass Child [Base]
          (defk m [self y] {:pre [(: self Child) (: y int)] :post [(: % int)]} (+ 1 (.m (super) y))))
        """
    )
    assert mod.subject.__qualname__ == "subject"
    assert mod.test_top.__qualname__ == "test_top"
    assert mod._top_tool_mcp_handler.__qualname__ == "_top_tool_mcp_handler"
    assert mod.top_tool.handler is mod._top_tool_mcp_handler
    assert mod.TopEffect.__qualname__ == "TopEffect"
    assert repr(mod.TopEffect(1)) == "TopEffect(n=1)"
    assert mod.top_helper.__qualname__ == "top_helper"
    # A module-level deftest records the pytest item as before (only a deftest inside a function does not record)
    assert any('"test_top"' in text for text in module_record_texts(mod))
    assert mod.Child.m.__qualname__ == "Child.m"
    assert _run(mod.Child().m(2)) == 3
    assert _run(mod.program) == 1
    assert "_doeff_do" in vars(mod)
