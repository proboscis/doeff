"""Names bound by a tuple target from a tuple literal of the same length (agora-redesign #4254).

doeff-hy's ``defk`` / ``deff`` with a typed ``:post`` contract declares ``_contract_result: T``
at the head of the function and binds every exit as ``(_contract_result,) = (value,)``
(a53309e37) — the one-element tuple target keeps Hy from moving the value's temporary name
onto the declared name.  The reader followed only ``name = value``, so the name an env builder
returns through stayed unknown: every handler a foundation installs inside such a ``defk``
went missing, and agora-controllers' closure checks reported the cluster handlers' effects
(``ReportMetrics``, ``ReadShared``, ``LeaseOp`` …) as gaps under ``unknown=('_contract_result',)``.

``(a, b) = (x, y)`` binds ``a`` to ``x`` and ``b`` to ``y`` exactly as two plain assignments
do, so the reader reads it element by element.
"""

import pytest
from doeff_effect_analyzer.handler_effects import analyze_env

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from doeff_vm import EffectBase


class Mark(EffectBase):
    pass


class Tick(EffectBase):
    pass
"""

HANDLERS_HY = """\
(require doeff-hy.macros [defhandler])
(import {pkg}.effects [Mark Tick])

(defhandler marker
  {:tags {:context "analyzer-test" :role "foundation"}}
  (Mark [] (resume None)))

(defhandler ticker
  {:tags {:context "analyzer-test" :role "foundation"}}
  (Tick [] (resume None)))
"""

ENVS_PY = """\
from {pkg}.handlers import marker, ticker


def one_element_tuple_env():
    (handlers,) = ([marker, ticker],)
    return handlers


def two_element_tuple_env():
    (first, second) = (marker, ticker)
    return [first, second]
"""

# The shape doeff-hy expands a typed ``:post`` contract to (a53309e37).
CONTRACT_ENVS_HY = """\
(require doeff-hy.macros [defk])
(import {pkg}.handlers [marker ticker])

(defk contract-env []
  {:pre [] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  [marker ticker])
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package(
        {
            "effects.py": EFFECTS_PY,
            "handlers.hy": HANDLERS_HY,
            "envs.py": ENVS_PY,
            "contract_envs.hy": CONTRACT_ENVS_HY,
        }
    )


@pytest.mark.parametrize(
    "builder",
    ["envs:one_element_tuple_env", "envs:two_element_tuple_env", "contract_envs:contract_env"],
)
def test_a_name_bound_from_a_same_length_tuple_is_followed(pkg: str, builder: str) -> None:
    env = analyze_env(f"{pkg}.{builder}")

    assert [h.name for h in env] == ["marker", "ticker"], [h.to_dict() for h in env]
    assert all(h.known for h in env), [h.to_dict() for h in env]
