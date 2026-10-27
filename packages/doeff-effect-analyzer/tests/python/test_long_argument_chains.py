"""A Program argument handed on through a long chain of wrappers, and one handed back to the same function.

Regression target (card ki-36f06eb1c65a): agora-controllers' production job foundation hands the job's
body through the host, its inside, the pool split (which receives the cluster handlers as a function
value) and doeff-cluster's ``with-cluster-handlers``, each wrapping it once more. The reader stopped
binding Program arguments at a fixed depth of 4, so at the fifth wrapper the body was unbound and every
closure check showed ``yielded a value that is not a call: body``. The bound exists so that recursion
through arguments ends; it now ends on the recursion itself (a place met again on the chain).
"""

import pytest
from doeff_effect_analyzer.program_effects import analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff_vm import EffectBase


@dataclass(frozen=True)
class Marked(EffectBase):
    pass
"""

FOUNDATIONS_HY = """\
(require doeff-hy.macros [defk <-])
(import collections.abc [Callable])
(import doeff [Program])
(import {pkg}.effects [Marked])

(defk work []
  {:pre [] :post [(: % "None")]}
  (<- (Marked))
  None)

(defk passing [body]
  {:pre [(: body Program)] :post [(: % "body の答え")]}
  (<- answer body)
  answer)

(defk innermost [body]
  {:pre [(: body Program)] :post [(: % "body の答え")]}
  (<- answer (passing body))
  answer)

(defk split [inner body]
  {:pre [(: inner Callable) (: body Program)] :post [(: % "body の答え")]}
  (<- answer (inner (passing body)))
  answer)

(defk layer-1 [body]
  {:pre [(: body Program)] :post [(: % "body の答え")]}
  (<- answer (split innermost (passing body)))
  answer)

(defk layer-2 [body]
  {:pre [(: body Program)] :post [(: % "body の答え")]}
  (<- answer (layer-1 (passing body)))
  answer)

(defk layer-3 [body]
  {:pre [(: body Program)] :post [(: % "body の答え")]}
  (<- answer (layer-2 (passing body)))
  answer)

(defk layer-4 [body]
  {:pre [(: body Program)] :post [(: % "body の答え")]}
  (<- answer (layer-3 (passing body)))
  answer)

(defk long-chain []
  {:pre [] :post [(: % "None")]}
  (<- answer (layer-4 (work)))
  answer)

(defk again [body]
  {:pre [(: body Program)] :post [(: % "body の答え")]}
  (<- (passing body))
  (<- answer (again (passing body)))
  answer)

(defk recursion []
  {:pre [] :post [(: % "None")]}
  (<- answer (again (work)))
  answer)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "foundations.hy": FOUNDATIONS_HY})


def _short(names) -> set[str]:
    return {name.rsplit(".", 1)[-1] for name in names}


def test_a_body_handed_through_many_wrappers_is_read_where_it_runs(pkg: str) -> None:
    report = analyze_program(f"{pkg}.foundations:long_chain")

    assert report.unresolved == ()
    assert _short(report.effect_names) == {"Marked"}


def test_a_body_handed_back_to_the_same_function_ends(pkg: str) -> None:
    # The counter-case: ``again`` hands its body to itself wrapped again. The reading ends where the
    # place ``(passing body)`` comes round a second time, and says it stopped there.
    report = analyze_program(f"{pkg}.foundations:recursion")

    assert _short(report.effect_names) == {"Marked"}
    assert report.unresolved != ()
