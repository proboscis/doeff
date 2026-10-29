"""契約 `(: x T)` と型付き束縛 `(<- x T e)` の T が、型引数つきの総称型を値に持つ名(型の別名)でも、実行時に TypeError で落ちず
外側の型で確かめること(card acp:kanban-issue:ki-8673c7bb5b0c)。

契約と束縛は書いた型を `doeff_hy._type_forms.runtime_type_form` で isinstance に渡せる形へ写すが、写せるのは字面の総称型だけで、
名は展開の時に値が分からないのでそのまま isinstance に渡る。名の値が `Callable[..., object]` や `FrozenMap[object] | tuple[object, ...]`
だと、どの値でも `TypeError: isinstance() argument 2 cannot be a parameterized generic` になった(2026-10-11 に本番で 2 度 —
agora-controllers の turn-host が別名 LiveLimitAnswer = (get Callable #(... object)) で 9 分止まり、受付が別名 FrozenJson で全部の
郵便に 500 を返した。使い手は字面へ展開して避け、FrozenJson は doeff-hy の側で実行時の名を外側の型の和にした — 6ab735f37)。
ここで守るのは名の値が何でも同じに扱う事: 実行時に確かめるのは外側の型だけ(#1790 の決め)で、外側の型が違う値は今までどおり
名指しの AssertionError で断る。型でない値を名に置いた契約は、何が型でないかを名乗る TypeError で断る。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

SOURCE = """\
(require doeff-hy.macros [defk <-])
(import collections.abc [Callable])
(import doeff_hy._frozen [FrozenMap])

;; 型引数つきの総称型を値に持つ名(使い手の module が書く別名の形)。
(setv Answer (get Callable #(... object)))
(setv Rows (| (get FrozenMap object) (get tuple #(object ...)) None))
(setv NotAType 3)

(defk called [answer]
  {:pre [(: answer Answer)] :post [(: % Answer)]}
  answer)

(defk rows-of [rows]
  {:pre [(: rows Rows)] :post [(: % bool)]}
  True)

;; 束縛の確かめだけを切り出すため、字面の型で文字列も関数も通す 1 段を挟む。
(defk echo [value]
  {:pre [(: value (| str (get Callable #(... object))))] :post [(: % (| str (get Callable #(... object))))]}
  value)

(defk bound-answer [value]
  {:pre [(: value (| str (get Callable #(... object))))] :post [(: % (get Callable #(... object)))]}
  (<- got Answer (echo value))
  got)

(defk misdeclared [value]
  {:pre [(: value NotAType)] :post [(: % bool)]}
  True)
"""


@pytest.fixture(scope="module")
def mod(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    import doeff_hy  # noqa: F401 - Hy の import hook を登録する

    root: Path = tmp_path_factory.mktemp("doeff_hy_aliased_generic_contract")
    (root / "aliased_generic_contract_probe.hy").write_text(SOURCE, encoding="utf-8")
    sys.path.insert(0, str(root))
    dont_write_before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        return importlib.import_module("aliased_generic_contract_probe")
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


def test_a_contract_naming_an_aliased_callable_accepts_a_function(mod: ModuleType) -> None:
    assert _run(mod.called(len)) is len


def test_a_contract_naming_an_aliased_union_of_generics_accepts_each_outer_type(mod: ModuleType) -> None:
    from doeff_hy._frozen import freeze_json_text

    for value in (freeze_json_text('{"a": 1}'), (1, 2), None):
        assert _run(mod.rows_of(value)) is True, value


def test_a_typed_bind_naming_an_aliased_callable_passes(mod: ModuleType) -> None:
    assert _run(mod.bound_answer(len)) is len


def test_an_aliased_contract_still_refuses_a_value_of_another_outer_type(mod: ModuleType) -> None:
    # 外側の型が違う値は契約の断り(TypeError でなく、名指しの AssertionError)。
    with pytest.raises(AssertionError, match="expected Answer"):
        _run(mod.called(3))
    with pytest.raises(AssertionError, match="expected Rows"):
        _run(mod.rows_of([1]))
    with pytest.raises(AssertionError, match="expected Answer"):
        _run(mod.bound_answer("x"))


def test_a_contract_naming_a_value_that_is_not_a_type_names_it(mod: ModuleType) -> None:
    with pytest.raises(TypeError, match="型でない"):
        _run(mod.misdeclared(1))
