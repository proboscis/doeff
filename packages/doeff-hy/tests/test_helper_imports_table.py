"""Checks that the runtime expansion of macros that reference `do` takes the location of `do` from the helper table (the `do` row `_runtime-do` of `_helper-imports` in macros.hy).
Details follow.

If the `do` row of the table is replaced, every macro expansion -- wherever it is written, in a module, a class body or a function body -- references
only the `do` at the replaced location. How it is referenced differs per macro: definition macros (defk, defp, deftest, defmcp-tool) use `_doeff_do` from the table import,
defhandler and handle in handle.hy use `_doeff_do` of `_do-import`, fnk uses an `__import__` that binds no name (`_do-reference`),
and validate uses `_doeff_validation_do` (`_validation-imports`). If a macro spells the location itself (e.g. importing `doeff.do` directly at runtime), the expansion checks for type checking (test_static_check.py) and the run checks (tests/public_api/test_mcp_tool_macro.py,
tests/test_nested_definition_scope.py, tests/test_handle_scope.py) still pass, but when the table is replaced
only that macro keeps wrapping with the old
`do`.
"""

import ast
import sys
import types
from collections.abc import Callable
from pathlib import Path

import doeff_hy  # noqa: F401 — registers extensions
import hy
import pytest
from doeff_core_effects import scheduled, try_handler
from doeff_hy import macros
from doeff_traverse import fail_handler, sequential

from doeff import run

# The replaced table `do`: wraps with doeff's do, pushes the __qualname__ of the wrapped function to WRAPPED, and marks the wrapped object with
# `__doeff_table_probe__`
_PROBE_MODULE = """
from doeff.do import do as _core_do

WRAPPED = []


def do(fn):
    wrapped = _core_do(fn)
    wrapped.__doeff_table_probe__ = fn.__qualname__
    WRAPPED.append(fn.__qualname__)
    return wrapped
"""

_REQUIRE = (
    "(require doeff-hy.macros [defk defp deftest defmcp-tool fnk validate check <- handle defhandler])\n"
    "(import dataclasses [dataclass])\n"
    "(import doeff [EffectBase Pure])\n"
    "(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)\n"
)

# Per-macro subject (binds subject), and the name under which the expansion binds `do` (None for references that bind no name)
_DEFINITIONS: dict[str, tuple[str, frozenset[str | None]]] = {
    "defk": ("(defk subject [x] {:pre [(: x int)] :post [(: % int)]} x)", frozenset({"_doeff_do"})),
    "defp": ("(defp subject {:post [(: % int)]} 1)", frozenset({"_doeff_do"})),
    "deftest": ("(deftest test-subject (assert True))", frozenset({"_doeff_do"})),
    "defmcp-tool": (
        '(defmcp-tool subject "Subject" [{:name "x" :type "string" :description "x"}] x)',
        frozenset({"_doeff_do"}),
    ),
    "defhandler": ("(defhandler subject (Bump [n] (resume (+ n 1))))", frozenset({"_doeff_do"})),
    "handle": (
        "(setv subject (handle (Bump 1) (Bump [n] (resume (+ n 1)))))",
        frozenset({"_doeff_do"}),
    ),
    "fnk": ("(setv subject (fnk [x] x))", frozenset({None})),
    "validate": (
        '(setv subject (validate (check = (! (Pure 1)) 1 :reason "same")))',
        frozenset({"_doeff_validation_do"}),
    ),
}

_PLACES = {
    "module": "{definition}",
    "class": "(defclass Box []\n  {definition})",
    "function": "(defn outer []\n  {definition}\n  None)",
}


# The replaced location is a module inside a package -- a name containing a dot, like the real `doeff.do` (Hy reads symbols containing dots as expressions, so
# an expansion that spells the location name from a symbol passes with a dotless name and breaks with a dotted one)
_PROBE = "doeff_hy_table_probe.table"


@pytest.fixture
def probe_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Replace the `do` row `_runtime-do` of the table with `do` of doeff_hy_table_probe.table (the macro looks it up by name at expansion time)."""
    package = tmp_path / "doeff_hy_table_probe"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "table.py").write_text(_PROBE_MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "doeff_hy_table_probe", raising=False)
    monkeypatch.delitem(sys.modules, _PROBE, raising=False)
    monkeypatch.setattr(macros, "_runtime_do", lambda: macros._RuntimeDo(_PROBE, "do"))
    import doeff_hy_table_probe.table  # type: ignore[import-not-found]  # test part placed in tmp_path

    return doeff_hy_table_probe.table


def _do_sources(tree: ast.Module) -> set[tuple[str, str | None]]:
    """#(module bound-name) of each place where the expansion references `do` -- `from m import do as a` and `__import__("m", fromlist=("do",))` (name is None)."""
    imports = {
        (node.module or "", alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "do"
    }
    dynamic = {
        (node.args[0].value, None)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "__import__"
        and isinstance(node.args[0], ast.Constant)
        and any(
            keyword.arg == "fromlist"
            and isinstance(keyword.value, ast.Tuple)
            and any(isinstance(e, ast.Constant) and e.value == "do" for e in keyword.value.elts)
            for keyword in node.keywords
        )
    }
    return imports | dynamic


@pytest.mark.parametrize("place", list(_PLACES))
@pytest.mark.parametrize("definition", list(_DEFINITIONS))
def test_runtime_expansion_takes_do_from_the_table(
    probe_table: types.ModuleType, definition: str, place: str
) -> None:
    """When the `do` row of the table is replaced, every place where the expansion references `do` refers only to the replaced location."""
    source, bindings = _DEFINITIONS[definition]
    tree = hy.compiler.hy_compile(
        hy.read_many(_REQUIRE + _PLACES[place].format(definition=source)), "__main__"
    )
    found = _do_sources(tree)
    assert {module for module, _ in found} == {_PROBE}, ast.unparse(tree)
    assert {name for _, name in found} == bindings, ast.unparse(tree)


def _run(program: object) -> object:
    """Run the Programs of validate and handle under the traverse handler."""
    return run(scheduled(try_handler(fail_handler(sequential(program)))))


# Where to return the defined value (a definition inside a function body is received as the outer function's result)
_RETURNING_PLACES = {
    "module": ("{definition}", lambda mod: mod.subject),
    "class": ("(defclass Box []\n  {definition})", lambda mod: mod.Box.subject),
    "function": ("(defn outer []\n  {definition}\n  subject)", lambda mod: mod.outer()),
}

# Advance until the defined value reaches the point where it goes through `do` (macros that wrap at definition time do nothing)
_USES: dict[str, Callable[[object], object]] = {
    "defk": lambda value: value,
    "defmcp-tool": lambda value: value,
    "defhandler": lambda value: value,
    "handle": _run,
    "fnk": lambda value: value(1),  # type: ignore[operator]  # the value of fnk is a 1-argument function
    "validate": _run,
}


# Exclude the combination of writing validate directly in a class body: the expansion import (`_doeff_validation_do`) becomes a class attribute and is invisible to the check thunk's
# function (a NameError regardless of the table -- validate belongs in a defk body, and the defk body in a class body is inside a function)
_USE_CASES = [
    (definition, place)
    for definition in _USES
    for place in _RETURNING_PLACES
    if (definition, place) != ("validate", "class")
]


@pytest.mark.parametrize(("definition", "place"), _USE_CASES)
def test_programs_are_built_by_the_tables_do(
    probe_table: types.ModuleType, definition: str, place: str
) -> None:
    """The replaced table `do` actually wraps the function -- checks not only the import but that the expansion reads that `do`."""
    template, defined = _RETURNING_PLACES[place]
    mod = types.ModuleType("helper_imports_table_subject")
    sys.modules["helper_imports_table_subject"] = mod
    for form in hy.read_many(_REQUIRE + template.format(definition=_DEFINITIONS[definition][0])):
        hy.eval(form, mod.__dict__, module=mod)
    _USES[definition](defined(mod))
    assert probe_table.WRAPPED, definition


@pytest.mark.parametrize("place", list(_RETURNING_PLACES))
@pytest.mark.parametrize(
    ("definition", "wrapped"),
    [("defk", lambda value: value), ("defmcp-tool", lambda value: value.handler)],
    ids=["defk", "defmcp-tool"],
)
def test_definitions_are_wrapped_by_the_tables_do(
    probe_table: types.ModuleType,
    definition: str,
    wrapped: Callable[[object], object],
    place: str,
) -> None:
    """The replaced table `do` wraps the defined function itself (the defk function, the defmcp-tool handler)."""
    template, defined = _RETURNING_PLACES[place]
    mod = types.ModuleType("helper_imports_table_subject")
    sys.modules["helper_imports_table_subject"] = mod
    for form in hy.read_many(_REQUIRE + template.format(definition=_DEFINITIONS[definition][0])):
        hy.eval(form, mod.__dict__, module=mod)
    target = wrapped(defined(mod))
    assert getattr(target, "__doeff_table_probe__", None) is not None, target
