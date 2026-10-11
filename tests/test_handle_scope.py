"""関数の本体の中に書いた handle・defhandler が、外の関数に局所の名を作らない事の検(card ki-907f779b3290)。

handle と defhandler の実行時の展開は、`_doeff_do`・`Resume`・`Transfer`・`Pass`・`WithHandler` と節の終わり方の検めを
書いた場所で import する。defk の本体(関数)の中でそれを import すると、名が関数全体の局所の名になり、同じ関数で先に
読む traverse / for/do の `_doeff_do` が UnboundLocalError で落ちていた(fnk・do! の ki-c67428538aec、入れ子の defk の
ki-964823cd7feb と同じ形)。

handle は、import・検め・handler の組み立てをすぐ呼ぶ関数に入れ、本文(第 1 引数)だけを書いた場所で評価する — 本文は
外の do の文脈に属する(`!` の yield は外の生成器の物)。defhandler は defk と同じく `_scoped-definition` で名だけを
外へ束ねる。module と class の本体は今までどおり並べる。
"""

import sys
import types

import doeff_hy  # noqa: F401 — registers extensions
import hy
import pytest
from doeff_core_effects import try_handler
from doeff_core_effects.scheduler import scheduled
from doeff_traverse.handlers import fail_handler, sequential

from doeff import run

# handle・defhandler の展開が書いた場所で import する名(handle.hy の `_do-import`・`_vm-imports`・`_clause-endings-import`、
# 節の外の効果を素通しする宣言の `_doeff-declare-passes`、defhandler の宣言の `doeff_hy`)
_HANDLE_IMPORT_NAMES = frozenset(
    {
        "_doeff_do",
        "Resume",
        "Transfer",
        "Pass",
        "WithHandler",
        "_doeff_check_clause_endings",
        "_doeff_check_clause_endings_once",
        "_doeff_declare_passes",
        "doeff_hy",
    }
)

# module の段の defk が在る(書き手の module の普通の形)— 外の関数が先に読む `_doeff_do` は module の globals に在る
_HEADER = """
(require doeff-hy.macros [defk do! <- traverse for/do handle defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects.effects [Resumption])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(defk bump-n [n] {:pre [(: n int)] :post [(: % int)]} (<- y (Bump n)) y)
"""


def _eval_module(code: str) -> types.ModuleType:
    """Hy の source を新しい module に読み込む — 検ごとに module の globals を分けるため。"""
    mod = types.ModuleType("handle_scope")
    sys.modules["handle_scope"] = mod
    for form in hy.read_many(_HEADER + code):
        hy.eval(form, mod.__dict__, module=mod)
    return mod


def _run(program: object) -> object:
    """handle を含む Program を、traverse / for/do の handler の下で走らせる。"""
    return run(scheduled(try_handler(fail_handler(sequential()(program)))))


_CASES = {
    # 依頼の形: traverse の `_doeff_do` の読みの後に handle が来る
    "handle-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (<- r (handle (bump-n (sum coll)) (Bump [n] (resume (+ n 100)))))
          r)
        """,
        112,
    ),
    # 本文の `!` は外の defk の do の文脈で走る(すぐ呼ぶ関数に本文を入れると、その関数が生成器になる)
    "handle-body-bang-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (<- r (handle (bump-n (! (do! (len coll)))) (Bump [n] (resume (+ n 100)))))
          r)
        """,
        103,
    ),
    # 走らない枝の handle の後に traverse が来る(import は走らなくても名を局所にする)
    "handle-in-untaken-branch-then-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (when (> (len items) 100) (handle (bump-n 1) (Bump [n] (resume n))))
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (sum coll))
        """,
        12,
    ),
    # 節は let の名と、handle の後で代え直した外の局所の名を、走った時の値で読む
    "handle-clause-reads-outer-names": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (setv step 7)
          (let [base 1000]
            (setv prog (handle (bump-n (sum coll)) (Bump [n] (resume (+ n base step)))))
            (setv step 70)
            (<- r prog)
            r))
        """,
        1082,
    ),
    "nested-handle-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (<- r (handle (handle (bump-n (len coll)) (Bump [n] :when (> n 5) (resume n)))
                  (Bump [n] (resume (+ n 100)))))
          r)
        """,
        103,
    ),
    # 素の defn の for/do の `_doeff_do` の読みの後に handle が来る
    "handle-in-defn-after-for-do": (
        """
        (defn make [items]
          (setv p (for/do (<- x (From items)) (* x 2)))
          (handle (bump-n (len items)) (Bump [n] (resume (+ n 100)))))
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- r (make items))
          r)
        """,
        103,
    ),
    # 入れ子の defhandler(defk と同じ `_scoped-definition` の形)
    "defhandler-in-defk-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (defhandler bump-h (Bump [n] (resume (+ n 100))))
          (<- r (bump-h (bump-n (sum coll))))
          r)
        """,
        112,
    ),
    "defhandler-with-params-in-defn-after-for-do": (
        """
        (defn make [items]
          (setv p (for/do (<- x (From items)) (* x 2)))
          (defhandler bump-h [base] (Bump [n] (resume (+ n base))))
          (bump-h 100))
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- r ((make items) (bump-n (len items))))
          r)
        """,
        103,
    ),
}


@pytest.mark.parametrize(("source", "expected"), _CASES.values(), ids=_CASES.keys())
def test_handle_inside_function_runs(source: str, expected: object) -> None:
    assert _run(_eval_module(source).subject([1, 2, 3])) == expected


@pytest.mark.parametrize(("source", "expected"), _CASES.values(), ids=_CASES.keys())
def test_outer_function_binds_no_handle_import(source: str, expected: object) -> None:
    """handle・defhandler が import する名は、外の関数の局所(と閉包の cell)に現れない — 現れると先の読みが未束縛になる。"""
    mod = _eval_module(source)
    for outer in (mod.subject, getattr(mod, "make", mod.subject)):
        code = getattr(outer, "__doeff_generator_function__", outer).__code__
        assert _HANDLE_IMPORT_NAMES.isdisjoint(code.co_varnames + code.co_cellvars)


def _module_globals_view(mod: types.ModuleType) -> dict[str, tuple[int, int | None]]:
    """module の globals の名ごとの (値の id, 入れ物なら要素の数) — 外の関数を呼んで globals に名や記録が増えたかを比べるため。"""
    return {
        name: (id(value), len(value) if isinstance(value, list | dict | set | tuple) else None)
        for name, value in vars(mod).items()
    }


@pytest.mark.parametrize(("source", "expected"), _CASES.values(), ids=_CASES.keys())
def test_handle_inside_function_leaves_module_globals_unchanged(
    source: str, expected: object
) -> None:
    """関数の中の handle・defhandler は、外の関数を何度呼んでも module の globals に名も記録も足さない — 包んだ文の並びは
    呼ぶたびに評価される(`_scoped-definition` の呼び手の負う事)。"""
    mod = _eval_module(source)
    before = _module_globals_view(mod)
    assert _run(mod.subject([1, 2, 3])) == expected
    assert _run(mod.subject([1, 2, 3])) == expected
    assert _module_globals_view(mod) == before


_HOSTS = {
    "handle": ("(defn host [] (handle (bump-n 1) (Bump [n] (resume (+ n 1)))))", []),
    "defhandler": ("(defn host [] (defhandler h (Bump [n] (resume n))) h)", ["h"]),
    "defhandler-with-params": ("(defn host [] (defhandler h [b] (Bump [n] (resume b))) h)", ["h"]),
}


@pytest.mark.parametrize(("source", "names"), _HOSTS.values(), ids=_HOSTS.keys())
def test_host_function_gets_only_its_own_names(source: str, names: list[str]) -> None:
    """handle・defhandler だけを書いた関数は、Hy が作る一意の名(`_hy_` の頭 — 展開の中の関数の名)と定義した名の外に、
    局所の名も閉包の cell も持たない(import する名の一覧に依らない検)。"""
    code = _eval_module(source).host.__code__
    assert [
        name for name in code.co_varnames + code.co_cellvars if not name.startswith("_hy_")
    ] == names


def test_handle_checks_clause_endings_before_the_body() -> None:
    """関数の中の handle も、節の終わり方の検めを本文の式より前に済ませる — 検めが落ちたら本文は評価されない
    (すぐ呼ぶ関数を本文より先に呼ぶ。Python は呼ぶ関数の式を引数より先に評価する)。本文を先に評価すると
    `body-evaluated` の AssertionError になる。検めの ClauseEndingError は TypeError の子(clause_endings.hy は .hy なので
    型検査から引けず、基の型と文言で受ける)。"""
    mod = _eval_module(
        """
        (defclass [(dataclass :frozen True)] Halt [EffectBase]
          (setv __doeff_resumption__ Resumption.NEVER))
        (defn body-evaluated [] (raise (AssertionError "本文が検めより先に評価された")))
        (defn subject [] (abs 1) (handle (body-evaluated) (Halt [] (resume 0))))
        """
    )
    with pytest.raises(TypeError, match=r"Halt は再開しない effect\(Resumption\.NEVER\)"):
        mod.subject()


def test_module_and_class_level_definitions_are_not_wrapped() -> None:
    """module と class の本体の handle・defhandler は包まない — 包むと `__qualname__`(pickle の名の引き)が変わる。"""
    mod = _eval_module(
        """
        (defhandler top-h [base] (Bump [n] (resume (+ n base))))
        (defclass Holder [] (defhandler inner-h [base] (Bump [n] (resume (+ n base)))))
        (setv program (handle (bump-n 1) (Bump [n] (resume (+ n 100)))))
        """
    )
    assert mod.top_h.__qualname__ == "top_h"
    assert mod.Holder.inner_h.__qualname__ == "Holder.inner_h"
    assert run(mod.top_h(5)(mod.bump_n(1))) == 6
    assert run(mod.program) == 101
    # module の段の handle の import は今までどおり module の globals に置く
    assert "WithHandler" in vars(mod)
