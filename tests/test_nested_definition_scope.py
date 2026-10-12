"""関数の本体の中に入れ子で書いた定義の macro(defk・defp・deftest・defmcp-tool・defeffect・deff・defsystem ほか
doeff_hy.macros が出す def の付く macro の全部と、doeff_hy.record の defrecord・defwire・defenum)が、外の関数に局所の名を
作らない事の検(card ki-964823cd7feb・defmcp-tool は card ki-9de0a906e39b・defeffect と deff と defsystem、macro の全部を
検体で数える事は card ki-93395c51de1e・defrecord と defwire は card ki-8a17293bc3e5)。

defk・defp・deftest の実行時の展開は、頭で `_doeff_do`・`_guard_statement_value`・`_doeff_effect_base` などの補助
(と `doeff_hy`・`pytest`・`_doeff_QuotedForms`)を import する。関数の本体の中でそれを import すると、名が関数全体の
局所の名になり、同じ関数で先に読む traverse / for/do の `_doeff_do`、本体の文の guard の `_guard_statement_value`、
最後の値の guard の `_doeff_effect_base` が UnboundLocalError で落ちていた(fnk・do! の同じ不具合は card ki-c67428538aec・
tests/test_fnk_do_bang_scope.py)。defmcp-tool も展開の頭で `_doeff_do`・`McpToolDef`・`McpParamSchema` を import する。
defeffect は `_doeff_effect_base`・`_doeff_dataclass`・`_doeff_ClassVar` などを、deff は宣言の `doeff_hy` を、defsystem は
`doeff_hy`・`doeff_cluster` を、defrecord は `doeff_hy`(record_runtime・頭の辞書の宣言)を、defwire は加えて
`_doeff_ClassVar` を import する。
module と class の本体の展開は包まない(`__qualname__` と引数なしの `super()` を保つ)。defhandler は tests/test_handle_scope.py
(card ki-907f779b3290)が検め、ここでは card ki-93395c51de1e の再現の手順の形だけを置く。

包む `_scoped-definition` の呼び手が負う事も検める(設計検証 lt-ZE5XJCF7YRE6ES4S8QDQASD1FF の失敗例): 関数の中の定義は
外の関数を呼ぶたびに評価されるので module の globals に記録を積まない事、class の本体の定義の関数が読む補助は
module の段に他の定義が無くても届く事。
"""

import ast
import sys
import types
from collections.abc import Callable
from pathlib import Path

import doeff_hy.conductor  # defworkflow の名を読む
import doeff_hy.macros  # registers extensions(定義の macro の名も読む)
import doeff_hy.record  # defrecord・defwire・defenum の名を読む
import hy
import pytest
from doeff_core_effects import try_handler
from doeff_core_effects.scheduler import scheduled
from doeff_hy.pytest_items import module_record_texts
from doeff_hy.static_check import Projection, project
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
        "_doeff_dataclass",
        "_doeff_ClassVar",
        "_doeff_cast",
        "_doeff_object",
        "doeff_core_effects",
        "doeff_cluster",
        "WithHandler",
        "Resume",
        "Transfer",
        "Pass",
        "_doeff_check_clause_endings_once",
        "_conductor_defworkflow",
        "_conductor_defphase",
        "_conductor_agent_bang",
    }
)

# module の段の defk が 1 つ在る(書き手の module の普通の形)— 入れ子の外の関数が先に読む補助は module の globals に在る
_HEADER = """
(require doeff-hy.macros [defk defp defpp deftest defmcp-tool defeffect deff defhandler defsystem defpipeline
                          <- traverse for/do])
(require doeff-hy.record [defrecord defwire defenum])
(require doeff-hy.conductor [defworkflow defphase agent!])
(import dataclasses [dataclass])
(import enum [StrEnum])
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
    # 素の defn の for/do の `_doeff_do` の読みの後に、入れ子の defhandler の import が来る(card ki-93395c51de1e の再現の手順 —
    # 直しは card ki-907f779b3290)
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
    # 走らない枝の入れ子の defeffect の後に、外の最後の値の guard(_doeff_effect_base)を読む(card ki-93395c51de1e の再現の手順)
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
    # 包んだ defeffect の型を外の defk が作る — :pre(__post_init__)・答えの分け方(doeff_core_effects の import)・凍結の仕上げ
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
    # 外の関数が module の段の doeff_hy を読んだ後に、入れ子の deff の宣言の import(doeff_hy)が来る
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
    # 外の関数が module の段の doeff_hy を読んだ後に、入れ子の defsystem の import(doeff_hy・doeff_cluster)が来る
    "defsystem-after-module-doeff-hy-read": (
        """
        (defn subject [x]
          (setv owner (. doeff_hy __name__))
          (defsystem lab [#^ object foundation] "lab" (job (fn [] None) :replicas 1))
          #(owner (. lab __name__) x))
        """,
        lambda mod: mod.subject(5),
        ("doeff_hy", "lab", 5),
    ),
    # 外の関数が module の段の doeff_hy を読んだ後に、入れ子の defrecord の record_runtime の import(doeff_hy)が来る
    # (card ki-8a17293bc3e5 の再現の手順)
    "defrecord-after-module-doeff-hy-read": (
        """
        (defn subject [] (setv owner (. doeff_hy __name__)) (defrecord Span #^ int start) owner)
        """,
        lambda mod: mod.subject(),
        "doeff_hy",
    ),
    # 同じ形の defwire(card ki-8a17293bc3e5 の再現の手順 — `_doeff_ClassVar` と doeff_hy.wire の import が来る)
    "defwire-after-module-doeff-hy-read": (
        """
        (defn subject [] (setv owner (. doeff_hy __name__)) (defwire Span {:names :camel} #^ int start) owner)
        """,
        lambda mod: mod.subject(),
        "doeff_hy",
    ),
    # 頭の辞書つきの defrecord を外の defk の文の guard の後に置き、型を作る — :check の __post_init__ が展開の import の
    # doeff_hy(require-check)を読み、検めが落ちる値は ValueError になる
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
    # 外の defk の traverse の後に入れ子の defwire — その型を解き手(parse-json)が wire の名で解く
    "defwire-in-defk-after-traverse-parses": (
        """
        (import doeff_hy.wire [parse-json])
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
    # 外の関数が module の段の `_conductor_defworkflow`(module の段の defworkflow の import)を読んだ後に、入れ子の
    # defworkflow の import が来る — 本体の phase・agent! の import も外の関数の局所にしない
    "defworkflow-after-module-conductor-read": (
        """
        (defworkflow module-flow :params {} :roles {})
        (defn subject []
          (setv before (. _conductor_defworkflow __name__))
          (defworkflow flow :params {} :roles {"worker" {"profile" "p"}}
            (defphase Build (agent! :role "worker" :class "test-verifiable" :prompt "p" :files #{"a.py"})))
          #(before (len (. flow body))))
        """,
        lambda mod: mod.subject(),
        ("defworkflow", 1),
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


# doeff_hy.macros が出す定義の macro(名が def で始まる macro)ごとの、関数の本体の中に置く検体と、その検体が外の関数に
# 束ねてよい名(定義した名)。macro を足したら検体も足す — 足さないと test_every_definition_macro_has_a_nested_sample が赤に
# なり、足した検体は test_function_with_only_a_definition_binds_only_its_names が補助の名の漏れを名に依らず検める
# (card ki-93395c51de1e — 定義の macro ごとに後から同じ漏れが見つかっていた: ki-964823cd7feb・ki-9de0a906e39b・ki-907f779b3290)。
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
    "defsystem": (
        '(defsystem lab [#^ object foundation] "lab" (job (fn [] None) :replicas 1))',
        frozenset({"lab"}),
    ),
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
    "defworkflow": (
        '(defworkflow flow :params {} :roles {"worker" {"profile" "p"}} '
        '(defphase Build (agent! :role "worker" :class "test-verifiable" :prompt "p" :files #{"a.py"})))',
        frozenset({"flow"}),
    ),
}


# 名の付く macro のうち、名を束ねない物(式の macro — defworkflow の本体に置く phase の値を返す)
_NOT_DEFINITION_MACROS = frozenset({"defphase"})


def _exported_definition_macros() -> set[str]:
    """`(require doeff-hy.macros [...])`・`(require doeff-hy.record [...])`・`(require doeff-hy.conductor [...])` で書き手が引ける
    定義の macro の名(handle.hy から出し直す defhandler を含む)。"""
    return {
        hy.unmangle(name)
        for module in (doeff_hy.macros, doeff_hy.record, doeff_hy.conductor)
        for name in vars(module)["_hy_macros"]
        if hy.unmangle(name).startswith("def") and hy.unmangle(name) not in _NOT_DEFINITION_MACROS
    }


def test_every_definition_macro_has_a_nested_sample() -> None:
    """doeff_hy.macros と doeff_hy.record が出す定義の macro には、関数の本体の中に置く検体が要る — 検体の無い macro は、
    補助の import が外の関数の局所の名になるかを誰も検めない(足した macro は検体を足すまで赤)。"""
    assert _exported_definition_macros() == set(_DEFINITION_SAMPLES)


@pytest.mark.parametrize(
    ("source", "defined"), _DEFINITION_SAMPLES.values(), ids=_DEFINITION_SAMPLES.keys()
)
def test_function_with_only_a_definition_binds_only_its_names(
    source: str, defined: frozenset[str]
) -> None:
    """定義 1 つだけを書いた関数の局所(と閉包の cell)は、定義した名と Hy が作る一意の名(`_hy_` の頭 — 展開の中の
    関数の名)の外に無い。補助の名の表(_SCOPE_LEAKING_NAMES)に依らないので、macro が新しい補助を import しても
    漏れを捕まえる。"""
    subject = _eval_module(f"(defn subject [] {source} None)").subject
    names = set(subject.__code__.co_varnames + subject.__code__.co_cellvars)
    assert {name for name in names if not name.startswith("_hy_")} == defined


def _module_globals_view(mod: types.ModuleType) -> dict[str, tuple[int, int | None]]:
    """module の globals の名ごとの (値の id, 入れ物なら要素の数) — 外の関数を呼んで globals に名や記録が増えたかを比べるため。"""
    return {
        name: (id(value), len(value) if isinstance(value, list | dict | set | tuple) else None)
        for name, value in vars(mod).items()
    }


@pytest.mark.parametrize(("source", "call", "expected"), _CASES.values(), ids=_CASES.keys())
def test_nested_definition_leaves_module_globals_unchanged(
    source: str, call: Callable[[types.ModuleType], object], expected: object
) -> None:
    """関数の中の定義は、外の関数を何度呼んでも module の globals に名も記録も足さない — 包んだ文の並びは呼ぶたびに
    評価されるので、記録を積む文を置くと呼んだ回数だけ積み増す(`_scoped-definition` の呼び手の負う事)。関数の中の
    deftest は pytest の item の記録(`__doeff_pytest_items__`)を積まない(card ki-93395c51de1e)。"""
    mod = _eval_module(source)
    before = _module_globals_view(mod)
    call(mod)
    call(mod)
    assert _module_globals_view(mod) == before


def _member_ids(value: object) -> tuple[int, ...] | None:
    """入れ物の要素(dict は鍵と値)の id の並び — 入れ物を替えずに中身を足す・差し替える登録を見分けるため。"""
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
    """定義を書いた module と、読み込み済みの doeff の module(名の頭が doeff)の globals の (module, 名) ごとの
    (値の id, 入れ物の要素の id) — 外の関数を呼ぶたびに、どこかの module に名・記録・登録が増えたかを比べるため
    (定義を書いた module だけを見ると、doeff の module の索引への登録を見逃す — 設計検証 lt-0YB1XJPKH406GNDWRZ4KND2Q9N の盲検 B)。"""
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
    """定義の macro の検体を関数の中に書くと、外の関数を何度呼んでも、定義を書いた module にも doeff の module にも
    名・記録・登録を足さない — 包んだ文の並びは呼ぶたびに評価される(`_scoped-definition` の呼び手の負う事)。
    module に 1 度だけ知らせる文は `_scoped-definition` の module-forms に渡し、関数の中では出さない。検体で数えるので、
    定義の macro を足すと検体と一緒にこの検も受ける(手で並べた _CASES に頼らない — 盲検 A)。初めの呼びは補助の module を
    読み込み、親の package に名を足しうるので、比べるのは 2 回目と 3 回目の呼びの前後。"""
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
    """型検査のための投影(doeff-hy-check が pyright に読ませる Python)で、関数の中の deftest は pytest の item の記録の文と、
    その import が作る `doeff_hy` の局所の名を外の関数に出さない — 実行時の展開は出さないので、投影も同じにする
    (設計検証 lt-0YB1XJPKH406GNDWRZ4KND2Q9N の盲検 A。module の段の記録は投影が記帳として外す)。"""
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
    """class の本体の定義の関数は、module に他の定義が無くても走る — class の本体では文の並びを包まず、import は
    class の属性になって、そこで定義した関数からは見えない。関数が名で読む補助は関数の globals に届ける。
    他の定義(module の段・同じ class の defk の `_install-guard-globals`)が module の globals に補助を入れて隠さない
    よう、定義を 1 つだけ置いた module で読む。"""
    assert call(_eval_module(source, header="(require doeff-hy.macros [defk defmcp-tool <-])")) == expected


# 定義した型の本体と method が名で読む補助(doeff_hy・_doeff_ClassVar ほか)は、class の本体に import しても届かない
# (入れ子の class の本体と method は名を引く時に外の class の段を飛ばす)。書き手の module には `doeff_hy` を import する
# 他の定義が無い — dataclass の import だけ(defrecord・defwire の既定の型が書き手の scope の dataclass を読む)。
_TYPE_ONLY_HEADER = """
(require doeff-hy.macros [defeffect])
(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass])
"""


def _refused(build: Callable[[], object], error: type[Exception]) -> bool:
    """build() が error で断られるか — 型を作る時の検め(:check・:pre)が働くかを見るため。"""
    try:
        build()
    except error:
        return True
    return False


_CLASS_BODY_TYPE_CASES = {
    # :check が展開の import の doeff_hy(require-check)を method から読み、検めが落ちる値は ValueError になる
    "defrecord-with-check": (
        "(defclass Outer [] (defrecord Span {:check [(>= start 0)]} #^ int start))",
        lambda mod: (
            repr(mod.Outer.Span(start=2)),
            _refused(lambda: mod.Outer.Span(start=-1), ValueError),
        ),
        ("Outer.Span(start=2)", True),
    ),
    # 展開が書く __repr__ の decorator(doeff_hy.record_runtime)を入れ子の型の本体が読む
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
    # 注記 `ClassVar[object]`(_doeff_ClassVar)を型の本体が読む — 読めないと dataclass が __doeff_answer__ を欄に数える
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
    """class の本体に直に書いた型を建てる定義(defrecord・defwire・defeffect)は、書き手の module が `doeff_hy` を持たなくても
    建ち、使える(card ki-8a17293bc3e5 の残り)。class の本体で import した名は外の class の属性になり、入れ子の型の本体と
    method は名を引く時に外の class の段を飛ばす — 展開の import を書いた所にだけ置くと、module の段に `doeff_hy` が在る
    module(他の定義が import する書き手の普通の形)でだけ動く。型の `__qualname__` は包まないので `Outer.<名>` のまま。"""
    assert "doeff_hy" not in vars(_eval_module("", header=_TYPE_ONLY_HEADER))
    mod = _eval_module(source, header=_TYPE_ONLY_HEADER)
    assert call(mod) == expected
    # module の globals へ置く文の補助の関数は、呼んだ後に class の属性として残さない
    assert not [name for name in vars(mod.Outer) if "publish" in name]


# 型検査の投影で、定義が展開の import を置いてよい名でない所(外の関数・外の class の本体の局所の名)— 根の module の名。
# 書き手がその名を module の段で読むので、定義の import が局所の名にすると pyright は module の段の名を未束縛と読む。
_PROJECTION_ROOT_NAMES = frozenset({"doeff_hy", "doeff_core_effects", "doeff_cluster"})

# 展開が `doeff_hy.<部分の module>.…` の綴りで読む、doeff_hy の部分の module(module の段の `import doeff_hy.<名>` が要る)
_PROJECTION_SUBMODULES = frozenset({"declarations", "record", "record_runtime", "wire"})

_STATIC_NESTED_SAMPLES = {
    "defrecord-with-header": (
        '(defrecord Span {:tags {:context "t" :role "type"} :check [(>= start 0)]} #^ int start)'
    ),
    "defwire": "(defwire Span {:names :camel :check [(>= start-at 0)]} #^ int start-at)",
    "defeffect": (
        "(defeffect Inner {:fields [(: x int)] :pre [(> x 0)] :answer (| int str) :absent [str] "
        ':tags {:context "t" :role "intent"}})'
    ),
    "deff": "(deff inner [y] {:pre [(: y int)] :post [(: % int)]} (+ y 1))",
}


def _projection_of(tmp_path: Path, body: str) -> ast.Module:
    """書き手の module(`doeff_hy` を import し、定義の macro を require する)に body を書いた型検査の投影の AST。"""
    source = tmp_path / "nested_static.hy"
    source.write_text(
        "(require doeff-hy.macros [deff defeffect])\n"
        "(require doeff-hy.record [defrecord defwire])\n"
        "(import dataclasses [dataclass])\n"
        "(import doeff_hy)\n" + body + "\n",
        encoding="utf-8",
    )
    projection = project(tmp_path, [tmp_path], source)
    assert isinstance(projection, Projection)
    return ast.parse(projection.text)


def _bound_by_imports(scope: ast.AST) -> set[str]:
    """scope の中(入れ子の関数・class の本体を含む)の import が束ねる名(`import a.b` は `a`・`as` があればその名)。"""
    return {
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(scope)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }


def _submodules_read(scope: ast.AST) -> set[str]:
    """scope が `doeff_hy.<部分の module>` の綴りで読む部分の module の名。"""
    return {
        node.attr
        for node in ast.walk(scope)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "doeff_hy"
        and node.attr in _PROJECTION_SUBMODULES
    }


def _module_level_imports(tree: ast.Module) -> set[str]:
    """module の段の `import a.b.c` が束ねる綴り(`a.b.c`)。"""
    return {
        alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names
    }


@pytest.mark.parametrize(
    "definition", _STATIC_NESTED_SAMPLES.values(), ids=_STATIC_NESTED_SAMPLES.keys()
)
def test_static_projection_of_function_definition_binds_no_module_name(
    tmp_path: Path, definition: str
) -> None:
    """型検査の投影(doeff-hy-check が pyright に読ませる Python)で、関数の中の定義は展開の import が束ねる根の module の名
    (`doeff_hy` ほか)を外の関数の局所の名にしない — 局所にすると、外の関数が先に module の段の `doeff_hy` を読む形を、
    pyright が未束縛と読む(card ki-8a17293bc3e5 の残り)。定義が読む部分の module(`doeff_hy.record` など)の import は
    module の段に置き、読む名は module の段の名として解ける。"""
    tree = _projection_of(
        tmp_path, f"(defn subject [] (setv owner (. doeff_hy __name__)) {definition} owner)"
    )
    subject = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "subject"
    )
    assert _PROJECTION_ROOT_NAMES.isdisjoint(_bound_by_imports(subject))
    assert {f"doeff_hy.{name}" for name in _submodules_read(subject)} <= _module_level_imports(tree)


@pytest.mark.parametrize(
    "definition", _STATIC_NESTED_SAMPLES.values(), ids=_STATIC_NESTED_SAMPLES.keys()
)
def test_static_projection_of_class_body_definition_binds_no_module_name(
    tmp_path: Path, definition: str
) -> None:
    """class の本体に直に書いた定義の投影も同じ — class の本体の import は外の class の属性で、pyright は入れ子の型の本体と
    method の名を module の段から引く(Python と同じ)。定義が読む名は module の段の import で解ける。"""
    tree = _projection_of(tmp_path, f"(defclass Outer [] {definition})")
    outer = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Outer"
    )
    assert _PROJECTION_ROOT_NAMES.isdisjoint(_bound_by_imports(outer))
    assert {f"doeff_hy.{name}" for name in _submodules_read(outer)} <= _module_level_imports(tree)


def test_module_and_class_level_definitions_are_not_wrapped() -> None:
    """module と class の本体の定義は包まない — 包むと `__qualname__`(pickle の名の引き)と引数なしの super() が変わる。"""
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
    # module の段の deftest は今までどおり pytest の item を記録する(関数の中の deftest だけが記録しない)
    assert any('"test_top"' in text for text in module_record_texts(mod))
    assert mod.Child.m.__qualname__ == "Child.m"
    assert _run(mod.Child().m(2)) == 3
    assert _run(mod.program) == 1
    assert "_doeff_do" in vars(mod)
