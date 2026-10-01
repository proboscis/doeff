"""Programs handed over in a tuple / list and taken out of it inside the callee.

Regression target (agora-redesign #1265, from #1178): a foundation of the shape
``(defk listeners [parts ports] …)`` receives one body per port as a tuple and runs
each under its own listener — ``(for [port ports] …)`` or ``(get ports 0)``.  The
reader followed a Program bound to a parameter by name, but not an element taken
out of a sequence of them: ``yield port`` was reported as "yielded a value that is
not a call", so the closure check saw none of the effects inside the ports.
"""

import pytest
from doeff_effect_analyzer.program_effects import Residual, analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff_vm import EffectBase


@dataclass(frozen=True)
class ReadBoard(EffectBase):
    prefix: str


@dataclass(frozen=True)
class WriteBoard(EffectBase):
    key: str


@dataclass(frozen=True)
class Nap(EffectBase):
    seconds: float
"""

FOUNDATIONS_HY = """\
(require doeff-hy.macros [defk <-])
(import collections.abc [Callable])
(import doeff [Program EffectBase with-handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [Gather Spawn])
(import {pkg}.effects [ReadBoard WriteBoard Nap])

(defk serve-reads []
  {:pre [] :post [(: % dict)]}
  (<- rows dict (ReadBoard "turn/"))
  rows)

(defk serve-writes []
  {:pre [] :post [(: % bool)]}
  (<- ok bool (WriteBoard "turn/c1/0"))
  ok)

(defk on-listener [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "the body's answer")]}
  (<- answer (with-handlers [(state)] body))
  answer)

;; The foundation's shape: every port runs as its own task, under its own listener.
(defk each-port [ports]
  {:pre [(: ports (| tuple list))] :post [(: % list)]}
  (setv tasks [])
  (for [port ports]
    (<- task (Spawn (on-listener port)))
    (.append tasks task))
  (<- answers list (Gather #* tasks))
  answers)

(defk listeners [parts ports]
  {:pre [(: parts float) (: ports (| tuple list))] :post [(: % list)]}
  (<- (Nap parts))
  (<- answers list (each-port ports))
  answers)

(defk first-port [parts ports]
  {:pre [(: parts float) (: ports (| tuple list))] :post [(: % "the first port's answer")]}
  (<- answer (on-listener (get ports 0)))
  answer)

(defk named-first-port [parts ports]
  {:pre [(: parts float) (: ports (| tuple list))] :post [(: % "the second port's answer")]}
  (setv chosen (get ports 1))
  (<- answer (on-listener chosen))
  answer)

(defk process-over [foundation]
  {:pre [(: foundation Callable)] :post [(: % int)]}
  (<- _closed (foundation 1.0 #((serve-reads) (serve-writes))))
  0)

(defk looped-process []
  {:pre [] :post [(: % int)]}
  (<- code int (process-over listeners))
  code)

(defk indexed-process []
  {:pre [] :post [(: % "the port's answer")]}
  (<- answer (first-port 1.0 #((serve-reads) (serve-writes))))
  answer)

(defk named-index-process []
  {:pre [] :post [(: % "the port's answer")]}
  (<- answer (named-first-port 1.0 [(serve-reads) (serve-writes)]))
  answer)

;; Hy's `match` as a bind operand expands to `_hy_anon_N = …` once per branch and
;; yields that local: each branch the local may hold is read.
(defk matched-process [read?]
  {:pre [(: read? bool)] :post [(: % "the chosen branch's answer")]}
  (<- answer (match read?
               True (serve-reads)
               _ (serve-writes)))
  answer)

(defk data-process []
  {:pre [] :post [(: % "the port's answer")]}
  (<- answer (first-port 1.0 #("not-a-program" "neither")))
  answer)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "foundations.hy": FOUNDATIONS_HY})


def _short(names) -> set[str]:
    return {name.rsplit(".", 1)[-1] for name in names}


def _leaving(report) -> Residual:
    """What leaves the Program with the Programs it spawns folded in — what a closure
    check reads (``doeff_cluster.foundation.foundation_check`` folds carried Programs the same way)."""
    return report.residual_with(lambda _carrier: True)


def test_a_port_taken_by_a_for_loop_is_read_as_each_element(pkg: str) -> None:
    leaving = _leaving(analyze_program(f"{pkg}.foundations:looped_process"))

    assert leaving.unresolved == ()
    assert {"ReadBoard", "WriteBoard", "Nap"} <= _short(leaving.effect_names)


def test_a_port_taken_by_a_constant_index_is_read_as_that_element(pkg: str) -> None:
    report = analyze_program(f"{pkg}.foundations:indexed_process")

    assert _leaving(report).unresolved == ()
    # What runs here is the element at the index; the element the callee does not run
    # is handed over (carried) — not dropped, since the callee may still run it elsewhere.
    assert _short(report.effect_names) == {"ReadBoard"}
    assert [_short(c.program.effect_names) for c in report.carried] == [{"WriteBoard"}]


def test_a_local_bound_once_to_an_element_is_read_as_that_element(pkg: str) -> None:
    report = analyze_program(f"{pkg}.foundations:named_index_process")

    assert _leaving(report).unresolved == ()
    assert _short(report.effect_names) == {"WriteBoard"}
    assert [_short(c.program.effect_names) for c in report.carried] == [{"ReadBoard"}]


def test_an_element_of_a_tuple_of_plain_data_stays_unresolved(pkg: str) -> None:
    # The counter-case: a tuple that holds no Program is not a sequence of Programs,
    # so what the callee takes out of it is reported, not read as an empty Program.
    leaving = _leaving(analyze_program(f"{pkg}.foundations:data_process"))

    assert leaving.effect_types == frozenset()
    assert [item.reason for item in leaving.unresolved] == ["yielded a value that is not a call"]


def test_a_local_assigned_on_each_branch_of_a_match_is_read_as_each_branch(pkg: str) -> None:
    leaving = _leaving(analyze_program(f"{pkg}.foundations:matched_process"))

    assert leaving.unresolved == ()
    assert _short(leaving.effect_names) == {"ReadBoard", "WriteBoard"}

