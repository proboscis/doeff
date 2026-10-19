"""Programs handed over as a generator expression / comprehension and run inside the callee.

Regression target (agora-redesign intake closure, request lt-QX2FM3MDS8EX0Q308A3NCQQYGJ):
doeff-traverse's ``sequence(programs)`` takes an iterable of Programs and runs each with
``value = yield program``.  Callers build that iterable as a generator expression —
``(sequence (gfor row rows (carrier-of-request row)))`` — which the reader did not read as
a sequence of Programs, so the yield inside ``sequence`` was reported as "yielded a value
that is not a call: program" and the closure check saw none of the effects of the elements.
A comprehension holds one element expression, read where the caller wrote it with the
comprehension's own variables unknown (they shadow any name outside it).
"""

import pytest
from doeff_effect_analyzer.program_effects import Residual, analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")
pytest.importorskip("doeff_traverse")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff_vm import EffectBase


@dataclass(frozen=True)
class ReadBoard(EffectBase):
    prefix: str


@dataclass(frozen=True)
class WriteBoard(EffectBase):
    key: str
"""

READS_HY = """\
(require doeff-hy.macros [defk <-])
(import doeff [Program])
(import doeff_traverse [sequence])
(import {pkg}.effects [ReadBoard WriteBoard])

(defk read-one [key]
  {:pre [(: key str)] :post [(: % dict)]}
  (<- rows dict (ReadBoard key))
  rows)

(defk write-pair [key value]
  {:pre [(: key str) (: value str)] :post [(: % bool)]}
  (<- ok bool (WriteBoard key))
  ok)

(defk run-it [body]
  {:pre [(: body Program)] :post [(: % "the body's answer")]}
  (<- answer body)
  answer)

(defk each-read [keys]
  {:pre [(: keys tuple)] :post [(: % tuple)]}
  (<- rows tuple (sequence (gfor key keys (read-one key))))
  rows)

(defk filtered-writes [keys values]
  {:pre [(: keys tuple) (: values tuple)] :post [(: % tuple)]}
  (<- oks tuple (sequence (gfor #(key value) (zip keys values) :if value (write-pair key value))))
  oks)

(defk set-in-clause [keys]
  {:pre [(: keys tuple)] :post [(: % tuple)]}
  (<- rows tuple (sequence (gfor key keys :setv shown (.upper key) (read-one shown))))
  rows)

(defk listed-reads [keys]
  {:pre [(: keys tuple)] :post [(: % tuple)]}
  (<- rows tuple (sequence (lfor key keys (read-one key))))
  rows)

;; The comprehension's variable is named like a module-level Program function: inside the
;; comprehension it is the iterated value, not that function.
(defk shadowing-variable [bodies]
  {:pre [(: bodies tuple)] :post [(: % tuple)]}
  (<- answers tuple (sequence (gfor read-one bodies (run-it read-one))))
  answers)

(defk plain-data [keys]
  {:pre [(: keys tuple)] :post [(: % tuple)]}
  (<- names tuple (sequence (gfor key keys (str key))))
  names)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "reads.hy": READS_HY})


def _short(names) -> set[str]:
    return {name.rsplit(".", 1)[-1] for name in names}


def _leaving(report) -> Residual:
    """What leaves the Program with the Programs it spawns folded in — what a closure check reads."""
    return report.residual_with(lambda _carrier: True)


def test_a_generator_of_program_calls_is_read_as_its_element(pkg: str) -> None:
    leaving = _leaving(analyze_program(f"{pkg}.reads:each_read"))

    assert leaving.unresolved == ()
    assert _short(leaving.effect_names) == {"ReadBoard"}


def test_a_filtered_generator_with_unpacked_variables_is_read_as_its_element(pkg: str) -> None:
    leaving = _leaving(analyze_program(f"{pkg}.reads:filtered_writes"))

    assert leaving.unresolved == ()
    assert _short(leaving.effect_names) == {"WriteBoard"}


def test_a_generator_with_a_setv_clause_is_read_as_its_element(pkg: str) -> None:
    leaving = _leaving(analyze_program(f"{pkg}.reads:set_in_clause"))

    assert leaving.unresolved == ()
    assert _short(leaving.effect_names) == {"ReadBoard"}


def test_a_list_comprehension_of_program_calls_is_read_as_its_element(pkg: str) -> None:
    leaving = _leaving(analyze_program(f"{pkg}.reads:listed_reads"))

    assert leaving.unresolved == ()
    assert _short(leaving.effect_names) == {"ReadBoard"}


def test_a_comprehension_variable_shadows_the_module_name_it_shares(pkg: str) -> None:
    # ``read-one`` inside the comprehension is the iterated value the reader cannot see,
    # so what ``run-it`` performs is reported, not read as the module's ``read-one`` body.
    leaving = _leaving(analyze_program(f"{pkg}.reads:shadowing_variable"))

    assert "ReadBoard" not in _short(leaving.effect_names)
    assert [item.reason for item in leaving.unresolved] == ["yielded a value that is not a call"]


def test_a_generator_of_plain_data_stays_unresolved(pkg: str) -> None:
    # The counter-case: a generator whose element builds no Program is not a sequence of
    # Programs, so what ``sequence`` yields out of it is reported, not read as nothing.
    leaving = _leaving(analyze_program(f"{pkg}.reads:plain_data"))

    assert leaving.effect_types == frozenset()
    assert [item.reason for item in leaving.unresolved] == ["yielded a value that is not a call"]
