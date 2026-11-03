"""`(! e)` が束ねた後、束ねの一時の名(`_hy_gensym_bound_N`)に Program を残さない。

`(! e)` は式の中に書くので、`<-` のように文の del を置けない(test_bind_releases_program.py)。展開は式の中で
名を None に戻してから値を読むか yield する。ここは束ねた後の frame の locals に Call も Pure も残らない事と、
答え(値・Pure の近道)と yield の数が変わらない事を見る。
"""

from __future__ import annotations

import ast
from types import ModuleType

import doeff_hy  # noqa: F401 - Hy の import hook を登録する
import hy
import pytest
from doeff_core_effects.handlers import reader

from doeff import run

_SOURCE = """
(require doeff-hy.macros *)
(import doeff-hy.macros *)
(import doeff_core_effects.effects [Ask])

(defk child [big]
  {:pre [(: big list)] :post [(: % int)]}
  (<- k str (Ask "k"))
  (len big))

(defk in-place [big]
  {:pre [(: big list)] :post [(: % int)]}
  (len big))

(defk parent [skip]
  {:pre [(: skip bool)] :post [(: % dict)]}
  (setv total 0)
  (for [i (range 3)]
    (setv total (+ total (! (child (list (range 10)))))))
  (setv answers #((! (child (list (range 1000))))
                  (+ 1 (! (in-place [1 2 3])))
                  (if skip 0 (! (child [1 2])))
                  (! (Ask "k"))
                  total))
  {"answers" answers
   "locals" (dict (locals))})
"""


def _module() -> ModuleType:
    module = ModuleType("doeff_hy_bang_releases_program_probe")
    hy.eval(hy.read_many(_SOURCE), module=module)
    return module


def _kept(skip: bool) -> dict[str, str]:
    out = run(reader(env={"k": "v"})(_module().parent(skip)))
    return {name: type(value).__name__ for name, value in out["locals"].items()}


@pytest.mark.parametrize("skip", [False, True])
def test_bang_leaves_no_program_in_the_frame(skip: bool) -> None:
    kept = _kept(skip)
    # 束ねの名は残っても None — yield した Call も、その場で答えた Pure も持たない
    assert {kept[name] for name in kept if "bound" in name} <= {"NoneType"}, kept
    assert not {"Call", "Pure"} & set(kept.values()), kept


def test_bang_answers_are_unchanged() -> None:
    assert run(reader(env={"k": "v"})(_module().parent(False)))["answers"] == (1000, 4, 2, "v", 30)
    assert run(reader(env={"k": "v"})(_module().parent(True)))["answers"] == (1000, 4, 0, "v", 30)


def test_bang_yields_once_per_bind() -> None:
    """The expansion holds one `yield` per `(! e)`: releasing the name adds no yield."""
    source = """
(require doeff-hy.macros *)
(import doeff-hy.macros *)
(defk f [x] {:pre [(: x int)] :post [(: % int)]} (+ (! (g x)) (! (g x))))
"""
    tree = hy.compiler.hy_compile(hy.read_many(source), ModuleType("doeff_hy_bang_yield_count"))
    assert sum(isinstance(node, ast.Yield) for node in ast.walk(tree)) == 2
