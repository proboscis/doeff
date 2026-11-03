"""``(! e)`` releases its bind's name in place, and the reader reads the same effects from it.

doeff-hy's ``(! e)`` expands to ``(b, (b := None))[0].value if (b := open_bind(e)).__class__ is
Pure else (yield (b, (b := None))[0])``: it cannot ``del`` the name in an expression, so it sets it
back to None before answering — else the frame holds the yielded Call until the line runs again.
The reader takes the release as no value of ``b`` (``b`` stays bound once, to the wrapper) and
reads the yielded ``(b, …)[0]`` as ``b``: a Program written with ``(! e)`` performs what the same
Program written with ``<-`` performs, and nothing is left unresolved.
"""

import ast

import pytest
from doeff_effect_analyzer._program_effects import _assignments, _released_name, analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff import EffectBase


@dataclass(frozen=True)
class Tick(EffectBase):
    pass


@dataclass(frozen=True)
class Nap(EffectBase):
    seconds: float
"""

PROGRAMS_HY = """\
(require doeff-hy.macros *)
(import doeff-hy.macros *)
(import .effects [Tick Nap])

(defk ticked [] {:pre [] :post [(: % int)]} (<- (Tick)) 1)
(defk napped [] {:pre [] :post [(: % int)]} (<- (Nap 1.0)) 2)

(defk with-arrow [n] {:pre [(: n int)] :post [(: % int)]}
  (<- a (ticked))
  (<- b (Nap 0.5))
  (setv total 0)
  (for [i (range n)]
    (<- c (napped))
    (setv total (+ total c)))
  (+ a total))

(defk with-bang [n] {:pre [(: n int)] :post [(: % int)]}
  (setv total 0)
  (for [i (range n)]
    (setv total (+ total (! (napped)))))
  (+ (! (ticked)) (if (> n 0) (! (Nap 0.5)) 0) total))

(defk nested-bang [] {:pre [] :post [(: % int)]}
  (+ (! (with-bang 2)) 1))
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "programs.hy": PROGRAMS_HY})


def _performed(pkg: str, program: str) -> set[str]:
    report = analyze_program(f"{pkg}.programs:{program}")
    assert report.unresolved == ()
    return {t.__name__ for t in report.effect_types}


def test_a_bang_bind_performs_what_the_same_arrow_bind_performs(pkg: str) -> None:
    assert _performed(pkg, "with_bang") == _performed(pkg, "with_arrow") == {"Tick", "Nap"}


def test_a_program_bound_by_a_bang_is_followed_into(pkg: str) -> None:
    assert _performed(pkg, "nested_bang") == {"Tick", "Nap"}


def test_the_release_is_not_a_value_the_bind_name_holds() -> None:
    """``b`` stays bound once (to the wrapper): its release adds no assignment, and the reader
    follows ``b`` as it follows ``<-``'s name.  A user's own ``(x := None)`` still binds ``x``."""
    released = ast.parse("(_hy_gensym_bound_3, (_hy_gensym_bound_3 := None))[0]", mode="eval").body
    release = next(node for node in ast.walk(released) if isinstance(node, ast.NamedExpr))
    assert _assignments(release) == ()
    assert _released_name(released) is not None
    assert _released_name(released).id == "_hy_gensym_bound_3"
    own = ast.parse("(x := None)", mode="eval").body
    assert isinstance(own, ast.NamedExpr)
    assert len(_assignments(own)) == 1
