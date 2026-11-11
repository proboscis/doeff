"""A Program held by a local bound once — a conditional, or a call of a local ``functools.partial``.

Regression target: agora-controllers' daily-verify task binds the Program it runs once,
``(val body (if (> pause-seconds 0) (parts.task … :pause-seconds …) (parts.task …)))``, and hands
``body`` to ``with-daily-verify-translation``; the reader followed a local only when it was bound to a
call, so the translation's ``(<- answer … body)`` showed ``yielded a value that is not a call: body``.
Its merge step binds ``(val mark (partial swept cause …))`` and performs ``(mark bounded)``; the reader
resolved ``mark`` to the partial object and showed ``yielded a call to something that is neither an
effect nor a Program function: mark``.  A local bound once is read through the expression it was bound
to (both branches of a conditional), and a call of a local partial as the call of its function with the
partial's arguments in front.  A local bound to what a ``yield`` answered is not a Program: it is not
read through its value.
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


@dataclass(frozen=True)
class Mark(EffectBase):
    tag: str
"""

READS_HY = """\
(require doeff-hy.macros [defk <-])
(import functools [partial])
(import doeff [Program])
(import {pkg}.effects [ReadAll ReadOne Mark])

(defk read-all []
  {:pre [] :post [(: % "None")]}
  (<- (ReadAll))
  None)

(defk read-one []
  {:pre [] :post [(: % "None")]}
  (<- (ReadOne))
  None)

(defk answer-with [from-records]
  {:pre [(: from-records Program)] :post [(: % "answer of from-records")]}
  (<- answer from-records)
  answer)

(defk marked [tag from-records]
  {:pre [(: tag str) (: from-records Program)] :post [(: % "answer of from-records")]}
  (<- (Mark tag))
  (<- answer from-records)
  answer)

(defk tagged [tag]
  {:pre [(: tag str)] :post [(: % "None")]}
  (<- (Mark tag))
  None)

(defk chosen-and-performed [whole]
  {:pre [(: whole bool)] :post [(: % "None")]}
  (val body (if whole (read-all) (read-one)))
  (<- answer body)
  answer)

(defk chosen-and-handed-on [whole]
  {:pre [(: whole bool)] :post [(: % "None")]}
  (val body (if whole (read-all) (read-one)))
  (<- answer (answer-with body))
  answer)

(defk marked-by-partial [tag]
  {:pre [(: tag str)] :post [(: % "None")]}
  (val mark (partial tagged tag))
  (<- (mark))
  None)

(defk run-by-partial [tag]
  {:pre [(: tag str)] :post [(: % "None")]}
  (val mark (partial marked tag))
  (<- answer (mark (read-all)))
  answer)

(defk front-program-by-partial []
  {:pre [] :post [(: % "None")]}
  (val run (partial answer-with (read-one)))
  (<- answer (run))
  answer)
"""

ANSWERS_PY = """\
from doeff import do

from {pkg}.effects import ReadAll, ReadOne
from {pkg}.reads import answer_with


@do
def answer_performed(whole):
    got = (yield ReadAll()) if whole else (yield ReadOne())
    yield got


@do
def answer_handed_on(whole):
    got = yield ReadAll()
    yield answer_with(got)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "reads.hy": READS_HY, "answers.py": ANSWERS_PY})


def _short(names) -> set[str]:
    return {name.rsplit(".", 1)[-1] for name in names}


@pytest.mark.parametrize("program", ["chosen_and_performed", "chosen_and_handed_on"])
def test_a_local_bound_once_to_a_conditional_is_read_as_both_branches(pkg: str, program: str) -> None:
    report = analyze_program(f"{pkg}.reads:{program}")

    assert report.unresolved == ()
    assert _short(report.effect_names) == {"ReadAll", "ReadOne"}


def test_a_call_of_a_local_partial_is_read_as_a_call_of_its_function(pkg: str) -> None:
    report = analyze_program(f"{pkg}.reads:marked_by_partial")

    assert report.unresolved == ()
    assert _short(report.effect_names) == {"Mark"}


def test_a_call_of_a_local_partial_binds_the_arguments_it_is_given_after_the_partials(pkg: str) -> None:
    # ``(mark (read-all))`` hands ``(read-all)`` to ``marked``'s second parameter, which it performs.
    report = analyze_program(f"{pkg}.reads:run_by_partial")

    assert report.unresolved == ()
    assert _short(report.effect_names) == {"Mark", "ReadAll"}


def test_a_program_a_local_partial_puts_in_front_is_read(pkg: str) -> None:
    report = analyze_program(f"{pkg}.reads:front_program_by_partial")

    assert report.unresolved == ()
    assert _short(report.effect_names) == {"ReadOne"}


def test_a_local_bound_to_a_yields_answer_is_not_read_through_its_value(pkg: str) -> None:
    # ``got`` holds what the yield answered, not the yielded Program: performing it stays unresolved,
    # and the yields inside its value are read only where they are written.
    report = analyze_program(f"{pkg}.answers:answer_performed")

    assert _short(report.effect_names) == {"ReadAll", "ReadOne"}
    assert [(u.reason, u.text) for u in report.unresolved] == [
        ("yielded a value that is not a call", "got")
    ]


def test_a_local_bound_to_a_yields_answer_is_not_handed_on_as_a_program(pkg: str) -> None:
    report = analyze_program(f"{pkg}.answers:answer_handed_on")

    assert _short(report.effect_names) == {"ReadAll"}
    assert [u.text for u in report.unresolved] == ["from_records"]
