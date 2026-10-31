"""A clause guarded by ``:when`` answers only the effects its guard lets in; the rest go outward.

``(EffectBase [] :when (not (isinstance effect Nap)) …)`` — the fence shape — expands to
``if isinstance(effect, EffectBase): if not guard: yield Pass(effect, k) else: <body>``: a ``Nap``
reaches the clause and is passed on, so it leaves the handler.  The reader took the clause for
every effect it names and never looked at its guard, so a ``Nap`` that crossed a fence was read as
answered by it and the Program's residual was empty (found by the design).
The guard is read per effect that arrives: a class test decides
(``isinstance`` / ``not`` / ``and`` / ``or``) and the effect it certainly sends on leaves the
handler.  A guard that tests anything else (a field of the effect, a call) is decided only at run
time: the clause is taken to answer the effect, as a clause without a guard does — most guards in an
application are of that kind (``(Ask [key] :when (= key …))``), and reading each as "may pass" would
report a gap for every such handler.

A clause that sends an effect on has run what comes before its guard: a guard that reads a session
value is tested after the value is taken (``Get``, its initialisation, ``Put``), and those effects
leave with it (``Clause.before_guard``).  Each call of a definition is read under the values it
gives (a guard naming a class by an argument), and the flow (``pass_through``) asks a clause only
whether it sends an effect on and what it performs — never the source (design check lt-XNEJ).
"""

import pytest
from doeff_effect_analyzer._handler_effects import analyze_handler, check_coverage
from doeff_effect_analyzer._program_effects import (
    AllTest,
    AnyTest,
    Basis,
    ClassTest,
    Clause,
    EffectUse,
    Escape,
    HandlerEffects,
    Location,
    NotTest,
    PassTest,
    ProgramEffects,
    Residual,
    UnknownTest,
    analyze_program,
    pass_through,
)

from doeff import EffectBase

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


@dataclass(frozen=True)
class Zap(EffectBase):
    pass


@dataclass(frozen=True)
class ReadFlag(EffectBase):
    name: str


@dataclass(frozen=True)
class Relay(EffectBase):
    effect: object
    k: object
"""

PROGRAMS_HY = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.handle [handle defhandler with-handler])
(import doeff [EffectBase])
(import {pkg}.effects [Tick Nap Zap ReadFlag])

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

;; a test on the exact class is not a test the reader decides: the clause is taken to answer
(defhandler fence-exact-class
  (EffectBase [] :when (not (is (type effect) Nap)) (raise (ValueError "fenced"))))
(defk through-fence-exact-class [] {:pre [] :post [(: % int)]}
  (<- r (with-handler [fence-exact-class] (three)))
  r)

;; the guard reads a session value: its Get / initialisation / Put run before the guard, so they
;; leave even for the Nap the guard sends on
(defk nap-only [] {:pre [] :post [(: % int)]} (<- (Nap 1.0)) 1)
(defhandler maintenance-gate
  (session val closed (! (ReadFlag "maintenance")))
  (EffectBase []
    :when (and closed (not (isinstance effect Nap)))
    (raise (ValueError "closed for maintenance"))))
(defk through-gate [] {:pre [] :post [(: % int)]}
  (<- r (with-handler [maintenance-gate] (nap-only)))
  r)

;; the fence is written once and read under what each call gives it
(defk sandboxed [passable] {:pre [(: passable type)] :post [(: % int)]}
  (<- r (handle (three) (EffectBase [] :when (not (isinstance effect passable)) (raise (ValueError "fenced")))))
  r)
(defk sandboxed-twice [] {:pre [] :post [(: % int)]}
  (<- a (sandboxed Nap))
  (<- b (sandboxed Tick))
  (+ a b))
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
from doeff import EffectBase

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


def test_a_guard_reads_the_values_the_call_gives_it(pkg: str) -> None:
    """The fence of ``sandboxed`` names its class by an argument: each call is read under its own
    (design check lt-XNEJ, blind B — a reading kept once per definition would answer the first
    call's class for every later call, and no other test here reads one fence twice)."""
    import importlib

    effects = importlib.import_module(f"{pkg}.effects")
    for first in ("Nap", "Tick"):
        for name in (first, "Tick" if first == "Nap" else "Nap"):
            report = analyze_program(
                f"{pkg}.programs:sandboxed", bindings={"passable": getattr(effects, name)}
            )
            assert {t.__name__ for t in report.effect_types} == {name}, (first, name)
    assert _left(pkg, "sandboxed_twice") == {"Nap", "Tick"}


def test_what_a_clause_does_before_its_guard_leaves_with_the_effect_the_guard_sends_on(
    pkg: str,
) -> None:
    """A guard that reads a session value is tested after the value is taken (its ``Get``, the
    initialisation and the ``Put`` run first), so they leave even for the ``Nap`` the guard sends on
    (design check lt-XNEJ, blind A — the first reading sent the ``Nap`` on and lost these)."""
    assert _left(pkg, "through_gate") == {"Get", "Put", "ReadFlag", "Nap"}


def test_a_test_on_the_exact_class_is_not_one_the_reader_decides(pkg: str) -> None:
    assert _left(pkg, "through_fence_exact_class") == set()


def test_threads_reading_the_same_fence_agree(pkg: str) -> None:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(8) as pool:
        answers = list(pool.map(lambda _: _left(pkg, "through_fence"), range(16)))
    assert answers == [{"Nap"}] * 16


# A branch nested in a clause that yields something other than ``Pass`` is not a guard.
RELAY_PY = """\
from doeff import do
from doeff.program import with_handlers
from doeff import EffectBase

from {pkg}.effects import Nap, Relay, Tick


def relaying(effect, k):
    if isinstance(effect, EffectBase):
        if isinstance(effect, Nap):
            return (yield Relay(effect, k))
        raise ValueError("fenced")


@do
def tick_and_nap():
    yield Nap(1.0)
    yield Tick()
    return 1


@do
def through_relaying():
    return (yield with_handlers([relaying], tick_and_nap()))
"""


def test_a_nested_branch_that_yields_something_other_than_pass_is_not_a_guard(make_package) -> None:
    pkg = make_package({"effects.py": EFFECTS_PY, "relay.py": RELAY_PY})
    assert _left_of(pkg, "relay", "through_relaying") == {"Relay"}


# --- the boundaries between the reading and the flow, with no source read ----------------------


class Wide(EffectBase):
    pass


class Narrow(Wide):
    pass


class Other(EffectBase):
    pass


class Ahead(EffectBase):
    pass


class Body(EffectBase):
    pass


_AT = Location("probe", 1)


def _performing(*effects: type) -> ProgramEffects:
    return ProgramEffects(target="probe", effects=tuple(EffectUse(e, _AT) for e in effects))


def _arrives(*effects: type) -> Residual:
    return Residual(escapes=tuple(Escape(e, EffectUse(e, _AT)) for e in effects))


@pytest.mark.parametrize(
    ("test", "arrived", "verdict"),
    [
        (ClassTest((Narrow,)), Narrow, True),
        (ClassTest((Wide,)), Narrow, True),  # an instance of a subclass is an instance
        (ClassTest((Narrow,)), Wide, None),  # an effect of the parent class may be a Narrow
        (ClassTest((Narrow,)), Other, False),
        (ClassTest((), complete=False), Other, None),  # a class the reader could not find
        (ClassTest((Narrow,), complete=False), Narrow, True),
        (ClassTest((Narrow,), complete=False), Other, None),
        (NotTest(ClassTest((Narrow,))), Other, True),
        (NotTest(ClassTest((Narrow,))), Wide, None),
        (AllTest((ClassTest((Wide,)), UnknownTest())), Narrow, None),
        (AllTest((ClassTest((Narrow,)), UnknownTest())), Other, False),
        (AnyTest((ClassTest((Narrow,)), UnknownTest())), Narrow, True),
        (AnyTest((ClassTest((Other,)), UnknownTest())), Narrow, None),
        (AnyTest((ClassTest((Other,)), ClassTest((Ahead,)))), Narrow, False),
        (UnknownTest(), Narrow, None),
    ],
)
def test_a_guard_test_decides_by_the_class_that_arrives(
    test: PassTest, arrived: type, verdict: bool | None
) -> None:
    assert test.holds_for(arrived) is verdict
    clause = Clause(Wide, _performing(Body), _AT, passes=test)
    assert clause.passes_on(arrived) is (verdict is True)  # not known → the clause answers


def test_pass_through_sends_on_what_a_clause_says_without_reading_any_source() -> None:
    """The flow asks a clause only whether it sends an effect on (``passes_on``) and what it does
    before that (``before_guard``) or otherwise (``emits``): a hand-made clause is enough."""
    fence = HandlerEffects(
        "hand-made fence",
        (
            Clause(
                EffectBase,
                _performing(Body),
                _AT,
                passes=ClassTest((Narrow,)),
                before_guard=_performing(Ahead),
            ),
        ),
        basis=Basis.CLAUSES,
    )
    left = pass_through(_arrives(Narrow, Other), [fence])
    assert left.effect_types == {Narrow, Ahead, Body}  # Narrow sent on with Ahead; Other answered
    assert {e.effect: e.by for e in left.escapes}[Narrow] == ""  # still the place that performed it


def test_a_clause_stored_before_its_guard_was_read_performs_nothing_before_it() -> None:
    import pickle

    stored = Clause(Wide, _performing(Body), _AT)
    old = Clause.__new__(Clause)
    old.__dict__.update(handles=Wide, _emits=_performing(Body), location=_AT)  # the old stored form
    assert not old.passes_on(Wide)
    assert old.performed_before_guard().effect_types == frozenset()
    guarded = Clause(
        Wide,
        _performing(Body),
        _AT,
        passes=NotTest(ClassTest((Narrow,))),
        before_guard=lambda: _performing(Ahead),
    )
    for clause in (stored, guarded):
        again = pickle.loads(pickle.dumps(clause))
        assert again.passes == clause.passes
        assert again.passes_on(Other) == clause.passes_on(Other)
        assert again.performed_before_guard().effect_types == (
            clause.performed_before_guard().effect_types
        )
