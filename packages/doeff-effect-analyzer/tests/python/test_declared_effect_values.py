"""A value the contract types as an effect, performed as it is.

A Program that performs effects it was handed — ``(for [answer answers] (<- answer))`` with
``:pre [(: answers (get tuple #(HttpRespond ...)))]``, or ``(<- read ask)`` with ``:pre [(: ask
(| ReadRows ListTables))]`` — performs one of the classes its contract declares (``defk`` writes
the ``:pre`` type as the parameter's annotation and checks it on entry).  The reader saw only a
name it could not resolve ("yielded a value that is not a call: answer").  When the caller's
argument does not bind the parameter, the annotation is what the value is known to be.
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

PROGRAMS_HY = """\
(require doeff-hy.macros [defk <-])
(import {pkg}.effects [Mark Tick])

(defk each-handed [handed]
  {:pre [(: handed (get tuple #((| Mark Tick) ...)))] :post [(: % None)] :tags {:context "analyzer-test" :role "program"}}
  (for [one handed]
    (<- one))
  None)

(defk the-asked [asked]
  {:pre [(: asked Mark)] :post [(: % None)] :tags {:context "analyzer-test" :role "program"}}
  (<- asked)
  None)

(defk not-effects [values]
  {:pre [(: values (get tuple #(int ...)))] :post [(: % None)] :tags {:context "analyzer-test" :role "program"}}
  (for [one values]
    (<- one))
  None)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "programs.hy": PROGRAMS_HY})


def _names(report) -> set[str]:
    return {name.rsplit(".", 1)[-1] for name in report.effect_names}


def test_an_element_of_a_parameter_typed_as_a_sequence_of_effects_performs_them(pkg: str) -> None:
    report = analyze_program(f"{pkg}.programs:each_handed")

    assert report.unresolved == (), report.to_dict()
    assert _names(report) == {"Mark", "Tick"}


def test_a_parameter_typed_as_an_effect_performs_it(pkg: str) -> None:
    report = analyze_program(f"{pkg}.programs:the_asked")

    assert report.unresolved == (), report.to_dict()
    assert _names(report) == {"Mark"}


def test_a_type_that_is_not_an_effect_is_not_followed(pkg: str) -> None:
    # Counter-example: elements typed as something else stay places the reader cannot follow.
    report = analyze_program(f"{pkg}.programs:not_effects")

    assert [item.reason for item in report.unresolved] == ["yielded a value that is not a call"]
    assert _names(report) == set()
