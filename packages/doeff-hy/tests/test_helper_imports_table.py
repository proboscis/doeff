"""定義の macro(defk・defp・deftest・defmcp-tool)の実行時の展開が、補助の import を表 `_helper-imports`(macros.hy)から
取る事の検(card ki-4848d81e0bc5・設計検証 lt-N19BACF5SXDRMKQ7EZD3AZA8FC の盲検 B)。

表を差し替えると、どの定義の macro の展開も、module・class の本体・関数の本体のどこに書いても、差し替えた import を出す。
定義の macro が補助の import を自分で綴ると(例: 型検査のための展開では何も出さず、実行時は `doeff.do` だけを import する)、
型検査のための展開の検(test_static_check.py)と実行の検(tests/public_api/test_mcp_tool_macro.py・
tests/test_nested_definition_scope.py)は通ったまま、表を替えた時にその macro だけが取り残される。
"""

import ast
import sys
import types
from collections.abc import Callable
from pathlib import Path

import doeff_hy  # noqa: F401 — registers extensions
import hy
import pytest
from doeff_hy import macros

# 差し替えた表: `do` の出所だけを替え、他の補助は本物の表と同じ
_PROBE_TABLE = (
    "(do (import doeff-hy-table-probe [do :as _doeff_do])"
    " (import doeff [EffectBase :as _doeff_effect_base])"
    " (import doeff-hy.macros [_install-guard-globals _guard-performed"
    " _guard-statement-value _doeff-check-program-return]))"
)

# 差し替えた表の `do`: doeff の do で包み、包んだ物に印 `__doeff_table_probe__`(包んだ関数の __qualname__)を付ける
_PROBE_MODULE = """
from doeff.do import do as _core_do


def do(fn):
    wrapped = _core_do(fn)
    wrapped.__doeff_table_probe__ = fn.__qualname__
    return wrapped
"""

_REQUIRE = "(require doeff-hy.macros [defk defp deftest defmcp-tool <-])\n"

_DEFINITIONS = {
    "defk": "(defk subject [x] {:pre [(: x int)] :post [(: % int)]} x)",
    "defp": "(defp subject {:post [(: % int)]} 1)",
    "deftest": "(deftest test-subject (assert True))",
    "defmcp-tool": '(defmcp-tool subject "Subject" [{:name "x" :type "string" :description "x"}] x)',
}

_PLACES = {
    "module": "{definition}",
    "class": "(defclass Box []\n  {definition})",
    "function": "(defn outer []\n  {definition}\n  None)",
}


@pytest.fixture
def probe_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """表 `_helper-imports` を、`do` を doeff_hy_table_probe から取る表に差し替える(展開の時に macro が名で引く)。"""
    (tmp_path / "doeff_hy_table_probe.py").write_text(_PROBE_MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "doeff_hy_table_probe", raising=False)
    monkeypatch.setattr(macros, "_helper_imports", lambda: hy.read(_PROBE_TABLE))
    import doeff_hy_table_probe  # type: ignore[import-not-found]  # tmp_path に置いた検の部品

    return doeff_hy_table_probe


@pytest.mark.parametrize("place", list(_PLACES))
@pytest.mark.parametrize("definition", list(_DEFINITIONS))
def test_runtime_expansion_takes_helper_imports_from_the_table(
    probe_table: types.ModuleType, definition: str, place: str
) -> None:
    """表を差し替えると、展開が `_doeff_do` を束ねる import は差し替えた表の物だけになる。"""
    source = _REQUIRE + _PLACES[place].format(definition=_DEFINITIONS[definition])
    tree = hy.compiler.hy_compile(hy.read_many(source), "__main__")
    sources = sorted(
        {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if (alias.asname or alias.name) == "_doeff_do"
        }
    )
    assert sources == ["doeff_hy_table_probe"], ast.unparse(tree)


# 定義した値を返す置き場(関数の本体の中の定義は外の関数の答えで受け取る)
_RETURNING_PLACES = {
    "module": ("{definition}", lambda mod: mod.subject),
    "class": ("(defclass Box []\n  {definition})", lambda mod: mod.Box.subject),
    "function": ("(defn outer []\n  {definition}\n  subject)", lambda mod: mod.outer()),
}


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
    """差し替えた表の `do` が、定義した関数(defk の関数・defmcp-tool の handler)を実際に包む — import だけでなく、
    decorator が表の `_doeff_do` を読む事まで確かめる。"""
    template, defined = _RETURNING_PLACES[place]
    mod = types.ModuleType("helper_imports_table_subject")
    sys.modules["helper_imports_table_subject"] = mod
    for form in hy.read_many(_REQUIRE + template.format(definition=_DEFINITIONS[definition])):
        hy.eval(form, mod.__dict__, module=mod)
    target = wrapped(defined(mod))
    assert getattr(target, "__doeff_table_probe__", None) is not None, target
