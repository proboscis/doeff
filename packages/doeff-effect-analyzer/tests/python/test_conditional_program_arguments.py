"""A Program argument chosen by a conditional, handed on through a function that performs it.

Regression target: agora-controllers' messaging API answers a read through the shared
read path ``replica-read-at``, handing it the Program that reads the records as
``(if asked.descendants (ask-chains-recorded …) (ask-reports-recorded …))``. The reader bound a parameter to
a Program argument only when the argument was a Program call (or a name / element bound to one), so the
conditional was unbound and the intake API's closure check showed ``yielded a value that is not a call:
from_records``. A conditional either branch of which builds a Program is read as both branches, as a
conditional is when it is yielded.
"""

import pytest
from doeff_effect_analyzer._program_effects import analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff import EffectBase


@dataclass(frozen=True)
class ReadAll(EffectBase):
    pass


@dataclass(frozen=True)
class ReadOne(EffectBase):
    pass
"""

READS_HY = """\
(require doeff-hy.macros [defk <-])
(import doeff [Program])
(import {pkg}.effects [ReadAll ReadOne])

(defk read-all []
  {:pre [] :post [(: % "None")]}
  (<- (ReadAll))
  None)

(defk read-one []
  {:pre [] :post [(: % "None")]}
  (<- (ReadOne))
  None)

(defk answer-with [from-records]
  {:pre [(: from-records Program)] :post [(: % "from-records の答え")]}
  (<- answer from-records)
  answer)

(defk chosen-read [whole]
  {:pre [(: whole bool)] :post [(: % "None")]}
  (<- answer (answer-with (if whole (read-all) (read-one))))
  answer)

(defk chosen-or-nothing [whole]
  {:pre [(: whole bool)] :post [(: % "None")]}
  (<- answer (answer-with (if whole (read-all) None)))
  answer)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "reads.hy": READS_HY})


def _short(names) -> set[str]:
    return {name.rsplit(".", 1)[-1] for name in names}


def test_a_program_argument_chosen_by_a_conditional_is_read_as_both_branches(pkg: str) -> None:
    report = analyze_program(f"{pkg}.reads:chosen_read")

    assert report.unresolved == ()
    assert _short(report.effect_names) == {"ReadAll", "ReadOne"}


def test_a_conditional_with_one_program_branch_is_read_as_that_branch(pkg: str) -> None:
    # The other branch is plain data: performing it adds nothing the reader must follow.
    report = analyze_program(f"{pkg}.reads:chosen_or_nothing")

    assert _short(report.effect_names) == {"ReadAll"}
