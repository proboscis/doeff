"""A ``defk`` / ``defhandler`` / ``handle`` written in a function body is read where it is used.

doeff-hy writes a definition in a function body inside a function it calls at once
(``nested = _hy_anon_9()`` — card ki-964823cd7feb), so the helpers its expansion imports do
not become locals of the outer function; a ``handle`` in a function body binds its installer
the same way (card ki-907f779b3290). The reader followed only a def written directly in
the body: after that change a ``defk`` calling a ``defk`` of its own body was left unresolved
and its effects were lost (``outer`` below read no ``Tick``). The function a def's qualname
names is the same in the tree the reader indexes and in the running process.

What the wrapper returns is read in the scope of the call that reaches it, so a definition
in a body sees the bindings of that call (``run_nested`` read with two different ``make``) —
not those of whichever call was read first. A ``defhandler`` in a body is wrapped twice (its
own builder inside the definition's wrapper), and a fence-shaped ``handle`` writes its
dispatcher inside ``declare_passes``; both are read by their clauses, as at the module level
(design check lt-WWJ71GHT99N2H1PST9W7TVGD9D, blind A and B).
"""

import importlib

import pytest
from doeff_effect_analyzer._program_effects import Basis, analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff import EffectBase


@dataclass(frozen=True)
class Tick(EffectBase):
    pass


@dataclass(frozen=True)
class Nap(EffectBase):
    seconds: float
"""

PROGRAMS_HY = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.handle [handle defhandler with-handler])
(import collections.abc [Callable])
(import doeff [EffectBase])
(import {pkg}.effects [Tick Nap])

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

(defk tick-once [] {:pre [] :post [(: % int)]} (<- (Tick)) 1)
(defk nap-once [] {:pre [] :post [(: % int)]} (<- (Nap 1.0)) 1)

(defk run-nested [make]
  {:pre [(: make Callable)] :post [(: % int)]}
  (defk step [] {:pre [] :post [(: % int)]} (<- r (make)) r)
  (<- r (step))
  r)

(defk both [] {:pre [] :post [(: % int)]}
  (<- a (run-nested tick-once))
  (<- b (run-nested nap-once))
  (+ a b))

(defk napping [] {:pre [] :post [(: % int)]} (<- (Nap 1.0)) (<- (Tick)) 1)

(defk local-defhandler [] {:pre [] :post [(: % int)]}
  (defhandler nap-clock (Nap [seconds] (resume None)))
  (<- r (with-handler [nap-clock] (napping)))
  r)

(defhandler fence-at-module
  (EffectBase [] :when (not (isinstance effect Nap)) (raise (ValueError "fenced"))))

(defk fenced-module [] {:pre [] :post [(: % int)]}
  (<- r (with-handler [fence-at-module] (napping)))
  r)

(defk fenced-inline [] {:pre [] :post [(: % int)]}
  (<- r (handle (napping) (EffectBase [] :when (not (isinstance effect Nap)) (raise (ValueError "fenced")))))
  r)
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


@pytest.mark.parametrize("first", ["tick_once", "nap_once"])
def test_a_definition_in_a_body_reads_the_bindings_of_each_call(pkg: str, first: str) -> None:
    programs = importlib.import_module(f"{pkg}.programs")
    expected = {"tick_once": {"Tick"}, "nap_once": {"Nap"}}
    order = [first, *(name for name in expected if name != first)]
    for name in order:
        report = analyze_program(f"{pkg}.programs:run_nested", bindings={"make": getattr(programs, name)})
        assert _types(report.effect_types) == expected[name], name
    assert _types(analyze_program(f"{pkg}.programs:both").effect_types) == {"Tick", "Nap"}


def test_a_defhandler_in_a_body_is_read_by_its_clauses(pkg: str) -> None:
    report = analyze_program(f"{pkg}.programs:local_defhandler")
    assert _types(report.effect_types) == {"Tick"}  # nap-clock answers Nap
    assert [[h.basis for h in scope.handlers] for scope in report.handled] == [[Basis.CLAUSES]]
    assert report.unresolved == ()


def test_a_fence_shaped_handle_in_a_body_is_read_as_the_module_level_handler_is(pkg: str) -> None:
    inline = analyze_program(f"{pkg}.programs:fenced_inline")
    module = analyze_program(f"{pkg}.programs:fenced_module")
    assert [[h.basis for h in scope.handlers] for scope in inline.handled] == [[Basis.CLAUSES]]
    assert [[h.basis for h in scope.handlers] for scope in module.handled] == [[Basis.CLAUSES]]
    assert inline.effect_types == module.effect_types
