"""A ``defk`` written in a function body is read where it is called.

doeff-hy writes a definition in a function body inside a function it calls at once
(``nested = _hy_anon_9()`` — card ki-964823cd7feb), so the helpers its expansion imports do
not become locals of the outer function. The reader followed only a def written directly in
the body: after that change a ``defk`` calling a ``defk`` of its own body was left unresolved
and its effects were lost (``outer`` below read no ``Tick``). The function a def's qualname
names is the same in the tree the reader indexes and in the running process.
"""

import importlib

import pytest
from doeff_effect_analyzer.program_effects import analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff_vm import EffectBase


@dataclass(frozen=True)
class Tick(EffectBase):
    pass
"""

PROGRAMS_HY = """\
(require doeff-hy.macros [defk <-])
(import {pkg}.effects [Tick])

(defk outer [x]
  {:pre [(: x int)] :post [(: % int)]}
  (defk nested [z]
    {:pre [(: z int)] :post [(: % int)]}
    (<- (Tick))
    z)
  (<- r (nested x))
  r)

(defn factory []
  (defk made [z]
    {:pre [(: z int)] :post [(: % int)]}
    (<- (Tick))
    z)
  made)

(setv held (factory))
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "programs.hy": PROGRAMS_HY})


def _types(types_) -> set[str]:
    return {t.__name__ for t in types_}


def test_a_defk_calling_a_defk_of_its_own_body_reads_that_body(pkg: str) -> None:
    report = analyze_program(f"{pkg}.programs:outer")
    assert _types(report.effect_types) == {"Tick"}
    assert report.unresolved == ()


def test_a_defk_a_function_returns_is_found_by_its_qualname(pkg: str) -> None:
    held = importlib.import_module(f"{pkg}.programs").held
    # factory.<locals>.<the function called at once>.<locals>.made
    assert held.__qualname__.startswith("factory.<locals>.")
    assert held.__qualname__.endswith(".<locals>.made")
    report = analyze_program(f"{pkg}.programs:held")
    assert _types(report.effect_types) == {"Tick"}
    assert report.unresolved == ()
