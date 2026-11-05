"""After `<-` binds, the temporary binding name (`_hy_gensym_bound_N`) is removed from the frame.

The temporary name holds the yielded Program (Call -- function and arguments). Leaving it keeps the arguments alive until the same line runs next, and a generator in a long loop
(dispatch-turns in agora-controllers) kept holding the table of the previous iteration. Here we check that neither the Call nor the temporary binding name remains in the frame locals after
binding, and that the answers (value, type check, the Pure shortcut) do not change.
"""

from __future__ import annotations

from types import ModuleType

import hy
import pytest

import doeff_hy  # noqa: F401 - registers the Hy import hook
from doeff import run
from doeff_core_effects import reader

_SOURCE = """
(require doeff-hy.macros *)
(import doeff-hy.macros *)
(import doeff_core_effects._effects [Ask])

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
    # The binding name (bound) is removed by del, and the answer name (answer) of the nameless (<- e) is back to None
    assert not [name for name in kept if "bound" in name], kept
    assert {kept[name] for name in kept if "answer" in name} <= {"NoneType"}, kept
    assert "Call" not in kept.values(), kept


def test_bind_answers_are_unchanged() -> None:
    out = run(reader(env={"k": "v"})(_module().parent()))
    assert out["answers"] == (1000, 10, 3, 2)


def test_bind_still_checks_the_declared_type() -> None:
    with pytest.raises(Exception, match="expected str, got int"):
        run(reader(env={"k": "v"})(_module().wrong_type()))
