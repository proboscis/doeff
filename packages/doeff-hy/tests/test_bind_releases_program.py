"""`<-` が束ねた後、束ねの一時の名(`_hy_gensym_bound_N`)を frame から外す(agora card ki-d5fb19be03db)。

一時の名は yield した Program(Call — 関数と引数)を持つ。残すと同じ行が次に走るまで引数を生かし、長い loop の
generator(agora-controllers の dispatch-turns)が前の周の表を持ち続けた。ここは束ねた後の frame の locals に
Call も束ねの一時の名も残らない事と、答え(値・型の照らし・Pure の近道)が変わらない事を見る。
"""

from __future__ import annotations

from types import ModuleType

import hy
import pytest

import doeff_hy  # noqa: F401 - Hy の import hook を登録する
from doeff import run
from doeff_core_effects.handlers import reader

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

(defk parent []
  {:pre [] :post [(: % dict)]}
  (<- n (child (list (range 1000))))
  (<- m int (child (list (range 10))))
  (<- p (in-place [1 2 3]))
  (<- (child (list (range 3))))
  {"answers" #(n m p (<- (child [1 2])))
   "locals" (dict (locals))})

(defk wrong-type []
  {:pre [] :post [(: % int)]}
  (<- m str (child [1]))
  0)
"""


def _module() -> ModuleType:
    module = ModuleType("doeff_hy_bind_releases_program_probe")
    hy.eval(hy.read_many(_SOURCE), module=module)
    return module


def test_bind_leaves_no_program_in_the_frame() -> None:
    out = run(reader(env={"k": "v"})(_module().parent()))
    kept = {name: type(value).__name__ for name, value in out["locals"].items()}
    # 束ねの名(bound)は del で消え、名の無い (<- e) の答えの名(answer)は None に戻っている
    assert not [name for name in kept if "bound" in name], kept
    assert {kept[name] for name in kept if "answer" in name} <= {"NoneType"}, kept
    assert "Call" not in kept.values(), kept


def test_bind_answers_are_unchanged() -> None:
    out = run(reader(env={"k": "v"})(_module().parent()))
    assert out["answers"] == (1000, 10, 3, 2)


def test_bind_still_checks_the_declared_type() -> None:
    with pytest.raises(Exception, match="expected str, got int"):
        run(reader(env={"k": "v"})(_module().wrong_type()))
