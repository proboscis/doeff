"""fnk・do! の展開が、書いた場所の scope に名を作らず、その scope の名を前と同じに読む事の検(card ki-c67428538aec)。

fnk・do! の実行時の展開は、前は `_doeff_do`・`_guard_statement_value`・`_doeff_effect_base` などの補助を書いた場所で
import していた。defk の本体(関数)の中でそれを import すると、名が関数全体の局所の名になり、同じ関数で先に読む
traverse / for/do の `_doeff_do`、本体の文の guard の `_guard_statement_value`、最後の値の guard の `_doeff_effect_base` が
UnboundLocalError で落ちていた。fold の素直な置き換え `(Reduce (fnk [acc v] …) init coll)` がちょうどこの位置に来る。

fnk は lambda を書いた場所で作り(引数の既定値はその scope で評価する — class の本体の名も読める)、`do` を呼ぶたびに
`__import__` で引く。do! は補助の import と Program の組み立てを、すぐ呼ぶ関数の中に置く。どちらも、書いた場所の module に
defk も `_doeff_do` の import も無くても動く(defk が module の globals に置く名に頼らない)。
"""

import sys
import types

import doeff_hy  # noqa: F401 — registers extensions
import hy
import hy.macros
import pytest
from doeff_core_effects import scheduled, try_handler
from doeff_hy import macros
from doeff_traverse.handlers import fail_handler, sequential

from doeff import run


def _helper_names() -> frozenset[str]:
    """展開が名で読む補助の名: `_helper-imports` の import が束ねる名と、defk が globals に置く outcomes の名。

    表を写さず `_helper-imports` から読む — 補助を足した時に、この検が新しい名も見るように。
    """
    names: set[str] = {hy.mangle(macros.HELPERS_NAME)}
    for statement in macros._helper_imports()[1:]:
        assert str(statement[0]) == "import", statement
        imported = list(statement[2])
        for index, item in enumerate(imported):
            renamed = index + 1 < len(imported) and imported[index + 1] == hy.models.Keyword("as")
            if isinstance(item, hy.models.Keyword) or renamed:
                continue
            names.add(hy.mangle(str(item)))
    return frozenset(names)


_HELPER_NAMES = _helper_names()

_DEFK_HEADER = """
(require doeff-hy.macros [defk fnk do! <- traverse for/do])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse.effects [Reduce])
"""

# defk を持たず、`_doeff_do` も import しない module の頭 — fnk・do! が自分で要る物を持つ事を見る。
_BARE_HEADER = """
(require doeff-hy.macros [fnk do! <-])
"""


def _eval_module(code: str, header: str = _DEFK_HEADER) -> types.ModuleType:
    mod = types.ModuleType("fnk_do_bang_scope")
    sys.modules["fnk_do_bang_scope"] = mod
    for form in hy.read_many(header + code):
        hy.eval(form, mod.__dict__, module=mod)
    return mod


def _run(program: object) -> object:
    return run(scheduled(try_handler(fail_handler(sequential()(program)))))


def test_helper_names_follow_helper_imports() -> None:
    """表は `_helper-imports` の別名(`:as` の後の名)を拾い、元の名を拾わない。"""
    assert {
        "_doeff_do",
        "_doeff_effect_base",
        "_guard_statement_value",
        "_doeff_outcomes",
    } <= _HELPER_NAMES
    assert "do" not in _HELPER_NAMES
    assert "EffectBase" not in _HELPER_NAMES


_CASES = {
    # 依頼の形: traverse の `_doeff_do` の読みの後に fnk が来る
    "fnk-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (<- total (Reduce (fnk [acc v] (+ acc v)) 0 coll))
          total)
        """,
        [1, 2, 3],
        12,
    ),
    "do-bang-after-for-do": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (for/do (<- x (From items)) (* x 3)))
          (<- n (do! (+ 1 1)))
          (<- total (Reduce (fnk [acc v] (+ acc v)) n coll))
          total)
        """,
        [1, 2, 3],
        20,
    ),
    # 本体の文の guard(_guard_statement_value)の読みの後に fnk が来る
    "fnk-after-statement-guard": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % int)]}
          (abs x)
          (setv f (fnk [y] (+ y 1)))
          (<- r (f x))
          r)
        """,
        5,
        6,
    ),
    # 走らない枝の fnk / do! の後に、最後の値の guard(_doeff_effect_base)を読む
    "fnk-in-untaken-branch": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % int)]}
          (when (> x 100) (fnk [y] y))
          x)
        """,
        5,
        5,
    ),
    "do-bang-in-untaken-branch": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % int)]}
          (when (> x 100) (do! x))
          x)
        """,
        5,
        5,
    ),
    # do! の中の文の guard の後に、内側の do! が来る
    "do-bang-inside-do-bang": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % int)]}
          (<- r (do! (abs x) (<- y (do! 2)) (+ y x)))
          r)
        """,
        5,
        7,
    ),
}


@pytest.mark.parametrize(("source", "arg", "expected"), _CASES.values(), ids=_CASES.keys())
def test_fnk_and_do_bang_inside_defk_run(source: str, arg: object, expected: object) -> None:
    mod = _eval_module(source)
    assert _run(mod.subject(arg)) == expected


@pytest.mark.parametrize(("source", "arg", "expected"), _CASES.values(), ids=_CASES.keys())
def test_defk_body_binds_no_helper_name(source: str, arg: object, expected: object) -> None:
    """補助の名は、defk の本体の関数の局所(と閉包の cell)に現れない — 現れると先の読みが未束縛になる。"""
    mod = _eval_module(source)
    code = mod.subject.__doeff_generator_function__.__code__
    assert _HELPER_NAMES.isdisjoint(code.co_varnames + code.co_cellvars)


_HOSTS = {
    "fnk": "(defn host [] (fnk [x] (<- y (do! x)) (+ y (! (do! 1)))))",
    "do-bang": "(defn host [] (do! (abs 1) (<- y (do! 2)) y))",
    "do-bang-with-post": "(defn host [] (do! {:post [(: % int)]} (<- y (do! 2)) y))",
}


@pytest.mark.parametrize("source", _HOSTS.values(), ids=_HOSTS.keys())
def test_host_function_gets_no_local_name(source: str) -> None:
    """fnk・do! だけを書いた関数は、Hy が作る一意の名(`_hy_` の頭 — 展開の中の関数の名)の外に、局所の名も閉包の
    cell も持たない(補助の一覧に依らない R2 の検)。"""
    mod = _eval_module(source, _BARE_HEADER)
    code = mod.host.__code__
    assert [
        name for name in code.co_varnames + code.co_cellvars if not name.startswith("_hy_")
    ] == []


_BARE_CASES = {
    # defk の無い module の fnk: 本体の <- と ! が module の globals の名に頼らない
    "module-fnk-bind-and-bang": (
        """
        (setv weight (fnk [x] (* x 10)))
        (setv subject (fnk [acc v] (<- w (weight v)) (+ acc w (! (weight 1)))))
        (setv program (subject 1 2))
        """,
        31,
    ),
    "module-do-bang": (
        """
        (setv program (do! (abs -1) (<- y (do! 2)) (+ y 1)))
        """,
        3,
    ),
    "module-do-bang-with-post": (
        """
        (setv program (do! {:post [(: % int)]} (<- y (do! 2)) (+ y 1)))
        """,
        3,
    ),
    "defn-fnk-default-reads-local": (
        """
        (defn make []
          (setv base 4)
          (fnk [x [b base]] (<- y (do! x)) (+ y b)))
        (setv program ((make) 1))
        """,
        5,
    ),
    # class の本体の fnk: 既定値は class の本体で評価する(module に同じ名が在っても class の値を読む)
    "class-fnk-default-reads-class-name": (
        """
        (setv RATE 1)
        (defclass Pricing []
          (setv RATE 3)
          (setv scale (fnk [x [rate RATE]] (* x rate))))
        (setv program ((. Pricing scale) 5))
        """,
        15,
    ),
    "class-fnk-default-reads-class-only-name": (
        """
        (defclass Pricing []
          (setv FACTOR 7)
          (setv scale (fnk [x [f FACTOR]] (<- y (do! x)) (* y f))))
        (setv program ((. Pricing scale) 5))
        """,
        35,
    ),
    "class-do-bang": (
        """
        (defclass Holder []
          (setv program (do! (abs -1) (<- y (do! 2)) (+ y 1))))
        (setv program (. Holder program))
        """,
        3,
    ),
}


@pytest.mark.parametrize(("source", "expected"), _BARE_CASES.values(), ids=_BARE_CASES.keys())
def test_fnk_and_do_bang_outside_defk_run(source: str, expected: object) -> None:
    """module の段・class の本体・普通の defn の fnk / do! は、defk も `_doeff_do` も無い module で、書いた場所の名を読む。"""
    mod = _eval_module(source, _BARE_HEADER)
    assert run(mod.program) == expected
