"""A ``Perform(effect)`` handed to a Program function as its body is read where the function runs it.

Regression target: agora-controllers' kanban API measured each read its handler sent on with
``(<- answer (timed-stage "board" (Perform effect)))`` — the clause hands ``Perform(effect)`` to a
``defk`` that runs its argument ``body`` between two clock reads. A ``Perform`` call yielded directly
is a doeff-vm control call the reader takes as it is (``(<- answer (Perform effect))``), but handed as
an argument it was not taken for a Program: ``body`` stayed unbound and the job's closure check showed
``yielded a value that is not a call: body``. ``Perform(...)`` builds the Program that performs its
effect, so an argument written as one is bound like any other Program argument and read where it runs —
the same as yielding it there.
"""

import pytest
from doeff_effect_analyzer._handler_effects import check_coverage
from doeff_effect_analyzer._program_effects import analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff import EffectBase


@dataclass(frozen=True)
class Marked(EffectBase):
    pass


@dataclass(frozen=True)
class Noted(EffectBase):
    pass
"""

PROGRAMS_HY = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.handle [defhandler with-handler])
(import doeff [Perform Program EffectBase])
(import {pkg}.effects [Marked Noted])

(defk work []
  {:pre [] :post [(: % "None")]}
  (<- (Marked))
  None)

(defk timed [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")]}
  (<- answer object body)
  (<- (Noted))
  answer)

;; the measured form: the clause hands the effect it sends on to ``timed``
(defhandler timed-meter
  (Marked [] (<- answer object (timed (Perform effect))) (resume answer)))

(defk timed-work []
  {:pre [] :post [(: % "None")]}
  (<- answer (with-handler [timed-meter] (work)))
  answer)

;; the same clause sending the effect on directly (what the measured form must read as, plus Noted)
(defhandler direct-meter
  (Marked [] (<- answer object (Perform effect)) (<- (Noted)) (resume answer)))

(defk direct-work []
  {:pre [] :post [(: % "None")]}
  (<- answer (with-handler [direct-meter] (work)))
  answer)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "programs.hy": PROGRAMS_HY})


def _names(gaps) -> set[str]:
    return {gap.effect.__name__ for gap in gaps}


def _coverage(pkg: str, program: str):
    """The program's own handler applied to what it performs (the closure check's reading)."""
    return check_coverage(analyze_program(f"{pkg}.programs:{program}"), [])


def test_a_perform_handed_as_a_body_is_read_where_it_runs(pkg: str) -> None:
    coverage = _coverage(pkg, "timed_work")

    assert coverage.unresolved == ()


def test_a_perform_handed_as_a_body_reads_as_the_perform_yielded_there(pkg: str) -> None:
    timed = _coverage(pkg, "timed_work")
    direct = _coverage(pkg, "direct_work")

    assert direct.unresolved == ()
    assert "Noted" in _names(direct.gaps)
    assert _names(timed.gaps) == _names(direct.gaps)
