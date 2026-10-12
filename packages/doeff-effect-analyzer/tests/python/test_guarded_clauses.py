"""A clause guarded by ``:when`` answers only the effects its guard lets in; the rest go outward.

``(EffectBase [] :when (not (isinstance effect Nap)) …)`` — the fence shape — expands to
``if isinstance(effect, EffectBase): if not guard: yield Pass(effect, k) else: <body>``: a ``Nap``
reaches the clause and is passed on, so it leaves the handler.  The reader took the clause for
every effect it names and never looked at its guard, so a ``Nap`` that crossed a fence was read as
answered by it and the Program's residual was empty (card ki-9a93072b861e; found by the design
check of card ki-1201d06f6324).  The guard is read per effect that arrives: a class test decides
(``isinstance`` / ``not`` / ``and`` / ``or``) and the effect it certainly sends on leaves the
handler.  A guard that tests anything else (a field of the effect, a call) is decided only at run
time: the clause is taken to answer the effect, as a clause without a guard does — most guards in an
application are of that kind (``(Ask [key] :when (= key …))``), and reading each as "may pass" would
report a gap for every such handler.
"""

import pytest
from doeff_effect_analyzer.handler_effects import analyze_handler, check_coverage
from doeff_effect_analyzer.program_effects import Basis, analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff_vm import EffectBase


@dataclass(frozen=True)
class Tick(EffectBase):
    pass


@dataclass(frozen=True)
class Nap(EffectBase):
    seconds: float


@dataclass(frozen=True)
class Zap(EffectBase):
    pass
"""

PROGRAMS_HY = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.handle [handle defhandler with-handler])
(import doeff [EffectBase])
(import {pkg}.effects [Tick Nap Zap])

(defk three [] {:pre [] :post [(: % int)]} (<- (Nap 1.0)) (<- (Tick)) (<- (Zap)) 1)

;; the fence: Nap passes, everything else is refused
(defhandler fence-nap
  (EffectBase [] :when (not (isinstance effect Nap)) (raise (ValueError "fenced"))))
(defk through-fence [] {:pre [] :post [(: % int)]}
  (<- r (with-handler [fence-nap] (three)))
  r)

;; the fence written inline in a body
(defk through-inline-fence [] {:pre [] :post [(: % int)]}
  (<- r (handle (three) (EffectBase [] :when (not (isinstance effect Nap)) (raise (ValueError "fenced")))))
  r)

;; the fence with an earlier clause, and what it passes named by a module constant
(setv PASSABLE Nap)
(defhandler fence-after-tick
  (Tick [] (resume None))
  (EffectBase [] :when (not (isinstance effect PASSABLE)) (raise (ValueError "fenced"))))
(defk through-fence-after-tick [] {:pre [] :post [(: % int)]}
  (<- r (with-handler [fence-after-tick] (three)))
  r)

;; what it passes given as an argument of the factory: the argument is not bound when the clauses
;; are read, so the guard is not known
(defhandler fence-of [passable]
  (Tick [] (resume None))
  (EffectBase [] :when (not (isinstance effect passable)) (raise (ValueError "fenced"))))
(defk through-fence-of [] {:pre [] :post [(: % int)]}
  (<- r (with-handler [(fence-of Nap)] (three)))
  r)

;; a tuple of classes passes
(defhandler fence-pair
  (EffectBase [] :when (not (isinstance effect #(Nap Zap))) (raise (ValueError "fenced"))))
(defk through-fence-pair [] {:pre [] :post [(: % int)]}
  (<- r (with-handler [fence-pair] (three)))
  r)

;; the guard lets only Tick in: Nap and Zap are passed on
(defhandler only-tick
  (EffectBase [] :when (isinstance effect Tick) (resume None)))
(defk through-only-tick [] {:pre [] :post [(: % int)]}
  (<- r (with-handler [only-tick] (three)))
  r)

;; a compound guard: Tick passes for sure, a Nap passes when its seconds are positive, Zap is refused
(defhandler fence-compound
  (EffectBase []
    :when (not (or (isinstance effect Tick) (and (isinstance effect Nap) (> effect.seconds 0))))
    (raise (ValueError "fenced"))))
(defk through-fence-compound [] {:pre [] :post [(: % int)]}
  (<- r (with-handler [fence-compound] (three)))
  r)

;; a guard that is not a class test: Nap may pass, may be answered
(defhandler long-naps
  (Nap [seconds] :when (> seconds 0) (resume None)))
(defk through-long-naps [] {:pre [] :post [(: % int)]}
  (<- r (with-handler [long-naps] (three)))
  r)

;; no guard: the catch-all answers everything
(defhandler answers-all
  (EffectBase [] (resume None)))
(defk through-answers-all [] {:pre [] :post [(: % int)]}
  (<- r (with-handler [answers-all] (three)))
  r)

;; no guard, one class: the others go outward
(defhandler answers-tick
  (Tick [] (resume None)))
(defk through-answers-tick [] {:pre [] :post [(: % int)]}
  (<- r (with-handler [answers-tick] (three)))
  r)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "programs.hy": PROGRAMS_HY})


def _left_of(pkg: str, module: str, program: str) -> set[str]:
    """The effect classes that leave ``program`` of ``module`` (a Program under a handler)."""
    report = analyze_program(f"{pkg}.{module}:{program}")
    assert report.unresolved == ()
    return {t.__name__ for t in report.effect_types}


def _left(pkg: str, program: str) -> set[str]:
    return _left_of(pkg, "programs", program)


@pytest.mark.parametrize(
    ("program", "leaves"),
    [
        ("through_fence", {"Nap"}),
        ("through_inline_fence", {"Nap"}),
        ("through_fence_after_tick", {"Nap"}),
        ("through_fence_pair", {"Nap", "Zap"}),
        ("through_only_tick", {"Nap", "Zap"}),
        ("through_answers_all", set()),
        ("through_answers_tick", {"Nap", "Zap"}),
    ],
)
def test_the_effects_a_guard_certainly_sends_on_leave_the_handler(
    pkg: str, program: str, leaves: set[str]
) -> None:
    assert _left(pkg, program) == leaves


@pytest.mark.parametrize(
    ("program", "leaves"),
    [
        # Tick is certainly passed; a Nap passes only when its seconds are positive, which the class
        # does not tell, so the clause is taken to answer it.
        ("through_fence_compound", {"Tick"}),
        # The argument of the factory is not bound when its clauses are read: the guard is not known.
        ("through_fence_of", set()),
        # The guard tests a field: the clause is taken to answer every Nap it can receive.
        ("through_long_naps", {"Tick", "Zap"}),
    ],
)
def test_a_guard_the_class_does_not_decide_is_taken_to_let_the_effect_in(
    pkg: str, program: str, leaves: set[str]
) -> None:
    assert _left(pkg, program) == leaves


def test_a_fence_is_read_by_its_clauses_and_leaves_a_gap_for_what_it_passes(pkg: str) -> None:
    import importlib

    programs = importlib.import_module(f"{pkg}.programs")
    fence = analyze_handler(programs.fence_nap, name="fence-nap")
    assert fence.basis is Basis.CLAUSES
    assert [clause.handles.__name__ for clause in fence.clauses] == ["EffectBase"]
    program = analyze_program(f"{pkg}.programs:three")
    coverage = check_coverage(program, [fence])
    assert [gap.effect.__name__ for gap in coverage.gaps] == ["Nap"]
    assert not coverage.complete


# A hand-written dispatcher whose guard sends the effect on with ``Pass`` imported under another
# name: the control value is told by what the name is, not by how it is spelt (doeff-hy writes it
# as ``_doeff_Pass``; the first reading matched the spelling ``Pass`` and lost every fence when the
# macro began to alias it).
ALIASED_PASS_PY = """\
from doeff import Pass as send_on
from doeff import do
from doeff.program import with_handlers
from doeff_vm import EffectBase

from {pkg}.effects import Nap, Tick, Zap


def fence_py(effect, k):
    if isinstance(effect, EffectBase):
        if isinstance(effect, Nap):
            return (yield send_on(effect, k))
        raise ValueError("fenced")
    return (yield send_on(effect, k))


@do
def three_py():
    yield Nap(1.0)
    yield Tick()
    yield Zap()
    return 1


@do
def through_fence_py():
    return (yield with_handlers([fence_py], three_py()))
"""


def test_a_guard_sends_the_effect_on_with_pass_under_any_name(make_package) -> None:
    pkg = make_package({"effects.py": EFFECTS_PY, "programs_py.py": ALIASED_PASS_PY})
    assert _left_of(pkg, "programs_py", "through_fence_py") == {"Nap"}
