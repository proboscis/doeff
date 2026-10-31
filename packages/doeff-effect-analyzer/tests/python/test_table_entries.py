"""A function taken out of a table a module global holds, then called.

A judgment that dispatches by kind keeps its folds in a module-level table and calls the one
that matches — ``(for [#(kind fold) FOLDERS] (when (= kind wanted) (<- (fold row))))`` — or
looks one up by name (``((get MAKERS name) base)``).  The reader saw the loop variable and the
subscript as names it could not resolve, so a job that reached such a dispatch was never closed
("yielded a call whose target is not a module-level name: fold(").  The call runs one of the
table's functions: each is read, as both branches of a conditional are.
"""

import pytest
from doeff_effect_analyzer._program_effects import analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from doeff import EffectBase


class Mark(EffectBase):
    pass


class Tick(EffectBase):
    pass
"""

TABLES_HY = """\
(require doeff-hy.macros [defk <-])
(import {pkg}.effects [Mark Tick])

(defk fold-mark [row]
  {:pre [(: row int)] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- (Mark))
  1)

(defk fold-tick [row]
  {:pre [(: row int)] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- (Tick))
  2)

(setv FOLDERS [#("mark" fold-mark) #("tick" fold-tick)])
(setv STEPS #(fold-mark fold-tick))
(setv MAKERS {"mark" fold-mark "tick" fold-tick})
(setv UNEVEN [#("mark" fold-mark) #("tick" fold-tick "extra")])

(defk fold-named [name row]
  {:pre [(: name str) (: row int)] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- (Tick))
  3)

(import functools [partial])
(setv PARTIAL-FOLDERS [#("mark" fold-mark) #("named" (partial fold-named "named"))])
(setv MARK-FOLDERS [#("mark" fold-mark)])
(setv TICK-FOLDERS [#("tick" fold-tick)])

(defk by-row [kind row]
  {:pre [(: kind str) (: row int)] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (for [#(folder-kind folder) FOLDERS]
    (when (= folder-kind kind)
      (<- folded int (folder row))
      (return folded)))
  0)

(defk each-step [row]
  {:pre [(: row int)] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (for [step STEPS]
    (<- _done int (step row)))
  0)

(defk by-name [name row]
  {:pre [(: name str) (: row int)] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- made int ((get MAKERS name) row))
  made)

(defk by-constant [row]
  {:pre [(: row int)] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- made int ((get MAKERS "tick") row))
  made)

(defk by-partial-row [kind row]
  {:pre [(: kind str) (: row int)] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (for [#(folder-kind folder) PARTIAL-FOLDERS]
    (when (= folder-kind kind)
      (<- folded int (folder row))
      (return folded)))
  0)

(defk by-two-tables [kind row]
  {:pre [(: kind str) (: row int)] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (for [#(folder-kind folder) MARK-FOLDERS]
    (when (= folder-kind kind)
      (<- _m int (folder row))))
  (for [#(folder-kind folder) TICK-FOLDERS]
    (when (= folder-kind kind)
      (<- _t int (folder row))))
  0)

(defk by-uneven-row [kind row]
  {:pre [(: kind str) (: row int)] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (for [#(folder-kind folder) UNEVEN]
    (when (= folder-kind kind)
      (<- folded int (folder row))
      (return folded)))
  0)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "tables.hy": TABLES_HY})


def _names(report) -> set[str]:
    return {name.rsplit(".", 1)[-1] for name in report.effect_names}


@pytest.mark.parametrize("program", ["by_row", "each_step", "by_name", "by_partial_row", "by_two_tables"])
def test_a_function_taken_out_of_a_module_table_runs_each_entry(pkg: str, program: str) -> None:
    report = analyze_program(f"{pkg}.tables:{program}")

    assert report.unresolved == (), report.to_dict()
    assert _names(report) == {"Mark", "Tick"}


def test_a_constant_key_names_its_one_entry(pkg: str) -> None:
    report = analyze_program(f"{pkg}.tables:by_constant")

    assert report.unresolved == (), report.to_dict()
    assert _names(report) == {"Tick"}


def test_a_row_of_another_shape_is_not_followed(pkg: str) -> None:
    # Counter-example: a row the loop cannot unpack into its two names — the call stays a place
    # the reader cannot follow (not counted as performing nothing).
    report = analyze_program(f"{pkg}.tables:by_uneven_row")

    assert [item.reason for item in report.unresolved] == [
        "yielded a call whose target is not a module-level name"
    ], report.to_dict()
