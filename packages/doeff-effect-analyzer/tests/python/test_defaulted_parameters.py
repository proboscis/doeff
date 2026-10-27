"""A parameter the caller leaves at its default: None (a judgment the body calls only when it is set) or a function.

Regression target (card ki-e912b1821351): agora-controllers' intake stream reads its rows through
``(read-intake fold * [kept None])``, whose fold calls ``(kept entry)`` only when ``kept`` is not None. A
caller that leaves ``kept`` out (``(read-intake fold)``) left the parameter unbound, so the delivery jobs'
and the durable keeper's closure checks showed ``yielded a call whose target is not a module-level name:
kept``. A left-out parameter is bound to its default when the default says what a call on it does: None
performs nothing (the call raises where it runs), a function runs. A data default stays unbound.
"""

import pytest
from doeff_effect_analyzer.program_effects import analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff import EffectBase


@dataclass(frozen=True)
class ReadRows(EffectBase):
    pass


@dataclass(frozen=True)
class Judged(EffectBase):
    pass
"""

READS_HY = """\
(require doeff-hy.macros [defk <-])
(import collections.abc [Callable])
(import {pkg}.effects [ReadRows Judged])

(defk keep-open? [row]
  {:pre [(: row int)] :post [(: % bool)]}
  (<- (Judged))
  True)

(defk read-rows [* [kept None]]
  {:pre [(: kept (| Callable None))] :post [(: % "None")]}
  (<- (ReadRows))
  (when (is-not kept None)
    (<- keep bool (kept 1)))
  None)

(defk read-rows-judged [* [kept keep-open?]]
  {:pre [(: kept Callable)] :post [(: % "None")]}
  (<- keep bool (kept 1))
  None)

(defk read-every-row []
  {:pre [] :post [(: % "None")]}
  (<- (read-rows))
  None)

(defk read-open-rows []
  {:pre [] :post [(: % "None")]}
  (<- (read-rows :kept keep-open?))
  None)

(defk read-by-the-default-judgment []
  {:pre [] :post [(: % "None")]}
  (<- (read-rows-judged))
  None)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "reads.hy": READS_HY})


def _short(names) -> set[str]:
    return {name.rsplit(".", 1)[-1] for name in names}


def test_a_judgment_left_at_its_default_none_performs_nothing(pkg: str) -> None:
    report = analyze_program(f"{pkg}.reads:read_every_row")

    assert report.unresolved == ()
    assert _short(report.effect_names) == {"ReadRows"}


def test_a_judgment_the_caller_passes_runs(pkg: str) -> None:
    report = analyze_program(f"{pkg}.reads:read_open_rows")

    assert report.unresolved == ()
    assert _short(report.effect_names) == {"ReadRows", "Judged"}


def test_a_judgment_left_at_its_default_function_runs_the_function(pkg: str) -> None:
    report = analyze_program(f"{pkg}.reads:read_by_the_default_judgment")

    assert report.unresolved == ()
    assert _short(report.effect_names) == {"Judged"}
