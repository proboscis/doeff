"""`do` を引く macro の実行時の展開が、`do` の置き場を補助の表(macros.hy の `_helper-imports` の `do` の行 `_runtime-do`)から
取る事の検(card ki-4848d81e0bc5・設計検証 lt-N19BACF5SXDRMKQ7EZD3AZA8FC の盲検 B、card ki-973b4e44d7bc)。

表の `do` の行を差し替えると、どの macro の展開も、module・class の本体・関数の本体のどこに書いても、差し替えた置き場の
`do` だけを引く。引き方は macro ごとに違う — 定義の macro(defk・defp・deftest・defmcp-tool)は表の import の `_doeff_do`、
handle.hy の defhandler と handle は `_do-import` の `_doeff_do`、fnk は名を束ねない `__import__`(`_do-reference`)、
validate は `_doeff_validation_do`(`_validation-imports`)。macro が置き場を自分で綴ると(例: 実行時は `doeff.do` を直に
import する)、型検査のための展開の検(test_static_check.py)と実行の検(tests/public_api/test_mcp_tool_macro.py・
tests/test_nested_definition_scope.py・tests/test_handle_scope.py)は通ったまま、表を替えた時にその macro だけが古い
`do` で包み続ける。
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

# 差し替えた表の `do`: doeff の do で包み、包んだ関数の __qualname__ を WRAPPED に積み、包んだ物に印
# `__doeff_table_probe__` を付ける
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

# macro ごとの検体(subject を束ねる)と、展開が `do` を束ねる名(名を束ねない引きは None)
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


# 差し替えた置き場は package の中の module — 本物の `doeff.do` と同じく点を含む名(Hy は点を含む記号を式に読むので、
# 置き場の名を記号から綴る展開は、点の無い名では通り、点の在る名で壊れる)
_PROBE = "doeff_hy_table_probe.table"


@pytest.fixture
def probe_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """表の `do` の行 `_runtime-do` を、doeff_hy_table_probe.table の `do` に差し替える(展開の時に macro が名で引く)。"""
    package = tmp_path / "doeff_hy_table_probe"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "table.py").write_text(_PROBE_MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "doeff_hy_table_probe", raising=False)
    monkeypatch.delitem(sys.modules, _PROBE, raising=False)
    monkeypatch.setattr(macros, "_runtime_do", lambda: macros.RuntimeDo(_PROBE, "do"))
    import doeff_hy_table_probe.table  # type: ignore[import-not-found]  # tmp_path に置いた検の部品

    return doeff_hy_table_probe.table


def _do_sources(tree: ast.Module) -> set[tuple[str, str | None]]:
    """展開が `do` を引く所の #(module 束ねる名) — `from m import do as a` と `__import__("m", fromlist=("do",))`(名は None)。"""
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
    """表の `do` の行を差し替えると、展開が `do` を引く所は、どれも差し替えた置き場の物だけになる。"""
    source, bindings = _DEFINITIONS[definition]
    tree = hy.compiler.hy_compile(
        hy.read_many(_REQUIRE + _PLACES[place].format(definition=source)), "__main__"
    )
    found = _do_sources(tree)
    assert {module for module, _ in found} == {_PROBE}, ast.unparse(tree)
    assert {name for _, name in found} == bindings, ast.unparse(tree)


def _run(program: object) -> object:
    """validate と handle の Program を、traverse の handler の下で走らせる。"""
    return run(scheduled(try_handler(fail_handler(sequential(program)))))


# 定義した値を返す置き場(関数の本体の中の定義は外の関数の答えで受け取る)
_RETURNING_PLACES = {
    "module": ("{definition}", lambda mod: mod.subject),
    "class": ("(defclass Box []\n  {definition})", lambda mod: mod.Box.subject),
    "function": ("(defn outer []\n  {definition}\n  subject)", lambda mod: mod.outer()),
}

# 定義した値を使って `do` を通る所まで進める(定義の時に包む macro は何もしない)
_USES: dict[str, Callable[[object], object]] = {
    "defk": lambda value: value,
    "defmcp-tool": lambda value: value,
    "defhandler": lambda value: value,
    "handle": _run,
    "fnk": lambda value: value(1),  # type: ignore[operator]  # fnk の値は 1 引数の関数
    "validate": _run,
}


# validate を class の本体に直に書く組は外す: 展開の import(`_doeff_validation_do`)が class の属性になり、check の thunk の
# 関数から見えない(表と関わらず NameError — validate は defk の本体に書く物で、class の本体の defk の本体は関数の中)
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
    """差し替えた表の `do` が、実際に関数を包む — import だけでなく、展開がその `do` を読む事まで確かめる。"""
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
    """差し替えた表の `do` が、定義した関数(defk の関数・defmcp-tool の handler)そのものを包む。"""
    template, defined = _RETURNING_PLACES[place]
    mod = types.ModuleType("helper_imports_table_subject")
    sys.modules["helper_imports_table_subject"] = mod
    for form in hy.read_many(_REQUIRE + template.format(definition=_DEFINITIONS[definition][0])):
        hy.eval(form, mod.__dict__, module=mod)
    target = wrapped(defined(mod))
    assert getattr(target, "__doeff_table_probe__", None) is not None, target
