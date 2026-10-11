"""関数の本体の中に入れ子で書いた defk・defp・deftest・defmcp-tool が、外の関数に局所の名を作らない事の検
(card ki-964823cd7feb・defmcp-tool は card ki-9de0a906e39b)。

defk・defp・deftest の実行時の展開は、頭で `_doeff_do`・`_guard_statement_value`・`_doeff_effect_base` などの補助
(と `doeff_hy`・`pytest`・`_doeff_QuotedForms`)を import する。関数の本体の中でそれを import すると、名が関数全体の
局所の名になり、同じ関数で先に読む traverse / for/do の `_doeff_do`、本体の文の guard の `_guard_statement_value`、
最後の値の guard の `_doeff_effect_base` が UnboundLocalError で落ちていた(fnk・do! の同じ不具合は card ki-c67428538aec・
tests/test_fnk_do_bang_scope.py)。defmcp-tool も展開の頭で `_doeff_do`・`McpToolDef`・`McpParamSchema` を import する。
module と class の本体の展開は包まない(`__qualname__` と引数なしの `super()` を保つ)。

包む `_scoped-definition` の呼び手が負う事も検める(設計検証 lt-ZE5XJCF7YRE6ES4S8QDQASD1FF の失敗例): 関数の中の定義は
外の関数を呼ぶたびに評価されるので module の globals に記録を積まない事、class の本体の定義の関数が読む補助は
module の段に他の定義が無くても届く事。
"""

import sys
import types
from collections.abc import Callable

import doeff_hy  # noqa: F401 — registers extensions
import hy
import pytest
from doeff_core_effects import try_handler
from doeff_core_effects.scheduler import scheduled
from doeff_traverse.handlers import fail_handler, sequential

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
    }
)

# module の段の defk が 1 つ在る(書き手の module の普通の形)— 入れ子の外の関数が先に読む補助は module の globals に在る
_HEADER = """
(require doeff-hy.macros [defk defp deftest defmcp-tool <- traverse for/do])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(defk _module-level [] {:pre [] :post [(: % int)]} 0)
"""


def _eval_module(code: str, header: str = _HEADER) -> types.ModuleType:
    """Hy の source を新しい module に読み込む — 検ごとに module の globals を分けるため。"""
    mod = types.ModuleType("nested_definition_scope")
    sys.modules["nested_definition_scope"] = mod
    for form in hy.read_many(header + code):
        hy.eval(form, mod.__dict__, module=mod)
    return mod


def _run(program: object) -> object:
    """入れ子の定義を含む Program を、traverse / for/do の handler の下で走らせる。"""
    return run(scheduled(try_handler(fail_handler(sequential()(program)))))


def _run_defk(arg: object) -> Callable[[types.ModuleType], object]:
    """外の定義 `subject` を arg で呼んだ Program を走らせる手 — 入れ子の定義を含む関数を実際に通すため。"""
    return lambda mod: _run(mod.subject(arg))


def _collection_values(mod: types.ModuleType) -> object:
    """for/do を返す素の defn `subject` の Collection を走らせ、値の並びにする — for/do の結果を比べるため。"""
    return _run(mod.subject([1, 2, 3])).valid_values


_CASES = {
    # 外の defk の文の guard(_guard_statement_value)の読みの後に、入れ子の defk の import が来る
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
    # 外の defk の traverse の `_doeff_do` の読みの後に、入れ子の defk の import が来る
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
    # 走らない枝の入れ子の defk の後に、外の最後の値の guard(_doeff_effect_base)を読む
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
    # 素の defn の for/do の `_doeff_do` の読みの後に、入れ子の defk の import が来る
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
    # 検の中の局所の補助の defk: deftest の本体の文の guard の後に入れ子の defk
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
    # 検を作る関数: 素の defn の for/do の後に入れ子の deftest(import pytest も外の関数の局所にしない)
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
    # 素の defn の for/do の `_doeff_do` の読みの後に、入れ子の defmcp-tool の import が来る(card ki-9de0a906e39b の再現の手順)
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
    # 外の defk の traverse の後に入れ子の defmcp-tool — 外の関数から tool の handler を呼べる
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
    # 関数の中の class の本体の defk は class の段の展開のまま(method は module の globals の補助を読む)
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
    """入れ子の定義の補助の名は、外の関数の局所(と閉包の cell)に現れない — 現れると先の読みが未束縛になる。"""
    subject = _eval_module(source).subject
    code = getattr(subject, "__doeff_generator_function__", subject).__code__
    assert _SCOPE_LEAKING_NAMES.isdisjoint(code.co_varnames + code.co_cellvars)


def _module_globals_view(mod: types.ModuleType) -> dict[str, tuple[int, int | None]]:
    """module の globals の名ごとの (値の id, 入れ物なら要素の数) — 外の関数を呼んで globals に名や記録が増えたかを比べるため。"""
    return {
        name: (id(value), len(value) if isinstance(value, list | dict | set | tuple) else None)
        for name, value in vars(mod).items()
    }


@pytest.mark.parametrize(
    ("source", "call", "expected"),
    [case for name, case in _CASES.items() if "deftest" not in name],
    ids=[name for name in _CASES if "deftest" not in name],
)
def test_nested_definition_leaves_module_globals_unchanged(
    source: str, call: Callable[[types.ModuleType], object], expected: object
) -> None:
    """関数の中の定義は、外の関数を何度呼んでも module の globals に名も記録も足さない — 包んだ文の並びは呼ぶたびに
    評価されるので、記録を積む文を置くと呼んだ回数だけ積み増す(`_scoped-definition` の呼び手の負う事)。
    deftest は呼ぶたびに pytest の item の記録を積む今の形があるので外す(card ki-93395c51de1e)。"""
    mod = _eval_module(source)
    before = _module_globals_view(mod)
    call(mod)
    call(mod)
    assert _module_globals_view(mod) == before


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
    """class の本体の定義の関数は、module に他の定義が無くても走る — class の本体では文の並びを包まず、import は
    class の属性になって、そこで定義した関数からは見えない。関数が名で読む補助は関数の globals に届ける。
    他の定義(module の段・同じ class の defk の `_install-guard-globals`)が module の globals に補助を入れて隠さない
    よう、定義を 1 つだけ置いた module で読む。"""
    assert call(_eval_module(source, header="(require doeff-hy.macros [defk defmcp-tool <-])")) == expected


def test_module_and_class_level_definitions_are_not_wrapped() -> None:
    """module と class の本体の定義は包まない — 包むと `__qualname__`(pickle の名の引き)と引数なしの super() が変わる。"""
    mod = _eval_module(
        """
        (defk subject [x] {:pre [(: x int)] :post [(: % int)]} x)
        (defp program {:post [(: % int)]} 1)
        (deftest test-top (assert True))
        (defmcp-tool top-tool "Top" [{:name "y" :type "integer" :description "y"}] y)
        (defclass Base [] (defn m [self y] y))
        (defclass Child [Base]
          (defk m [self y] {:pre [(: self Child) (: y int)] :post [(: % int)]} (+ 1 (.m (super) y))))
        """
    )
    assert mod.subject.__qualname__ == "subject"
    assert mod.test_top.__qualname__ == "test_top"
    assert mod._top_tool_mcp_handler.__qualname__ == "_top_tool_mcp_handler"
    assert mod.top_tool.handler is mod._top_tool_mcp_handler
    assert mod.Child.m.__qualname__ == "Child.m"
    assert _run(mod.Child().m(2)) == 3
    assert _run(mod.program) == 1
    assert "_doeff_do" in vars(mod)
