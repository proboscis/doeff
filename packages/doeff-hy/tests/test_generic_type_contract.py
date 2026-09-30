"""契約 `(: % (get tuple #(X ...)))` / `(: % (of dict K V))` と型付き束縛が実行時に通ること。

要素の型つきの総称型は isinstance に渡せない(`TypeError: isinstance() argument 2 cannot be a
parameterized generic`)。契約と `<-` の型付き束縛は `doeff_hy.type_forms.runtime_type_form` の 1 点で外側の型(tuple・dict)
へ写し、実行時には外側の型だけを確かめる(要素の型は静的な型検査が見る — #1790)。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

SOURCE = """\
(require doeff-hy.macros [defk deff <- defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Names [EffectBase]
  #^ int n)

(defk names [n]
  {:pre [(: n int)] :post [(: % (get tuple #(str ...)))]}
  (tuple (gfor i (range n) (str i))))

(defk names-of [n]
  {:pre [(: n int)] :post [(: % (of tuple str ...))]}
  (tuple (gfor i (range n) (str i))))

(defk counts [n]
  {:pre [(: n int)] :post [(: % (of dict str int))]}
  (dfor i (range n) (str i) i))

(defk counts-get [n]
  {:pre [(: n int)] :post [(: % (get dict #(str int)))]}
  (dfor i (range n) (str i) i))

(defk maybe-names [n]
  {:pre [(: n (| (get tuple #(int ...)) None))] :post [(: % (| (get tuple #(str ...)) None))]}
  (if (is n None) None (tuple (gfor i n (str i)))))

(defk bound-names [n]
  {:pre [(: n int)] :post [(: % (get tuple #(str ...)))]}
  (<- got (get tuple #(str ...)) (Names n))
  got)

(defhandler answers-names
  (Names [n] (resume (tuple (gfor i (range n) (str i))))))

(deff pure-names [n]
  {:pre [(: n int)] :post [(: % (get tuple #(str ...)))]}
  (tuple (gfor i (range n) (str i))))

(defk wrong-names [n]
  {:pre [(: n int)] :post [(: % (get tuple #(str ...)))]}
  (list (gfor i (range n) (str i))))

(defk wrong-counts [n]
  {:pre [(: n int)] :post [(: % (of dict str int))]}
  (tuple (range n)))

;; 途中の return も :post と guard を通る(agora-redesign #1823)。繰り返しの中・分岐の中の return と、入れ子の関数の return(外側の
;; 出口ではない — 書き換えない)。
(defk early-names [n]
  {:pre [(: n int)] :post [(: % (get tuple #(str ...)))]}
  (for [i (range n)]
    (when (= i 1) (return (tuple ["early"]))))
  (tuple (gfor i (range n) (str i))))

(defk early-wrong [n]
  {:pre [(: n int)] :post [(: % (get tuple #(str ...)))]}
  (for [i (range n)]
    (when (= i 1) (return [i])))
  (tuple []))

(defk early-effect [n]
  {:pre [(: n int)] :post [(: % (| Names None))]}
  (when (> n 0) (return (Names n)))
  None)

(deff early-pure-wrong [n]
  {:pre [(: n int)] :post [(: % str)]}
  (if (> n 0) (return n) "zero"))

(deff nested-return-is-not-an-exit [n]
  {:pre [(: n int)] :post [(: % str)]}
  (defn inner [] (return 7))
  (str (+ n (inner))))
"""


@pytest.fixture(scope="module")
def mod(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    import doeff_hy  # noqa: F401 - Hy の import hook を登録する

    root: Path = tmp_path_factory.mktemp("doeff_hy_generic_contract")
    (root / "generic_contract_probe.hy").write_text(SOURCE, encoding="utf-8")
    sys.path.insert(0, str(root))
    dont_write_before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        return importlib.import_module("generic_contract_probe")
    finally:
        sys.path.remove(str(root))
        sys.dont_write_bytecode = dont_write_before


def _run(program: object) -> object:
    """読み込んだ Hy の module の Program を走らせる(module の属性は型の上では object なので Program に絞る)。"""
    from doeff import Program, run

    match program:
        case Program():
            return run(program)
        case _:
            raise TypeError(f"Program でない値を走らせようとした: {program!r}")


def test_generic_tuple_return_contract_accepts_a_tuple(mod: ModuleType) -> None:
    assert _run(mod.names(2)) == ("0", "1")
    assert _run(mod.names_of(2)) == ("0", "1")
    assert mod.pure_names(2) == ("0", "1")


def test_generic_dict_return_contract_accepts_a_dict(mod: ModuleType) -> None:
    assert _run(mod.counts(2)) == {"0": 0, "1": 1}
    assert _run(mod.counts_get(1)) == {"0": 0}


def test_generic_inside_a_union_with_none(mod: ModuleType) -> None:
    assert _run(mod.maybe_names(None)) is None
    assert _run(mod.maybe_names((1, 2))) == ("1", "2")


def test_typed_bind_accepts_a_generic_tuple(mod: ModuleType) -> None:
    assert _run(mod.answers_names(mod.bound_names(2))) == ("0", "1")


def test_generic_contract_rejects_the_wrong_outer_type(mod: ModuleType) -> None:
    with pytest.raises(AssertionError):
        _run(mod.wrong_names(2))
    with pytest.raises(AssertionError):
        _run(mod.wrong_counts(2))


def test_an_early_return_passes_the_post_condition(mod: ModuleType) -> None:
    # 途中の return の値も :post に合えば通り、入れ子の関数の return は外側の出口ではない(agora-redesign #1823)。
    assert _run(mod.early_names(3)) == ("early",)
    assert _run(mod.early_names(1)) == ("0",)
    assert mod.nested_return_is_not_an_exit(1) == "8"


def test_an_early_return_is_checked_like_the_last_form(mod: ModuleType) -> None:
    # 反例: 途中の return で :post と違う型を返すと落ち、defk の途中の return が撃たない effect を返すと guard が落とす。
    with pytest.raises(AssertionError):
        _run(mod.early_wrong(3))
    with pytest.raises(AssertionError):
        mod.early_pure_wrong(1)
    assert mod.early_pure_wrong(0) == "zero"
    with pytest.raises(RuntimeError, match="unperformed effect"):
        _run(mod.early_effect(1))
