"""Test that the closure analysis reads only the clauses an effect has reached, and does not re-walk the same scope.

agora-controllers' closure test, each time it read a base handler, read the emits (the effects a clause yields inside) of every clause, and re-reported the handled scope and the carried
Program for every walk that reached it (of 452 clauses built from the deliver set only 118 were pulled; 7,000 to 17,500 reports covered
only 800 to 1,900 distinct bodies). A clause's emits are now read when an effect reaches it, and reports are remembered by the reader so the same thing is not rebuilt without changing the answer.

The two failure cases found by the blind design review are also rejected here: when two threads pull the clauses of one handler
at the same time, they walked the half-read facts of the reader the clauses share and dropped an effect (A). If handler reads are remembered per process,
the analysis answer and the count of sources read depended on an analysis run earlier in the same process (B -- the receiver of `:absent` was in fact remembered that way).
"""

import importlib
import pickle
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from doeff_effect_analyzer._handler_effects import analyze_handler, check_coverage
from doeff_effect_analyzer._program_effects import analyze_program, reading_sources

EFFECTS_PY = """\
from doeff import EffectBase


class Ping(EffectBase):
    pass


class Pong(EffectBase):
    pass


class Far(EffectBase):
    pass


class Tick(EffectBase):
    pass


class X(EffectBase):
    pass


class Y(EffectBase):
    pass


class Z(EffectBase):
    pass
"""

# A module read only by the Ping clause (to see from the record of sources read whether the clause's emits were read).
FAR_PY = """\
from doeff import do

from {pkg}.effects import Far


@do
def far_away():
    yield Far()
"""

HANDLERS_PY = """\
from doeff import Pass, Transfer

from {pkg}.effects import Ping, Pong
from {pkg}.far import far_away


def two_clauses(effect, k):
    if isinstance(effect, Ping):
        yield far_away()
        return (yield Transfer(k, None))
    if isinstance(effect, Pong):
        return (yield Transfer(k, None))
    yield Pass(effect, k)


# Two clauses call the same function (when two threads pull different clauses, they read the facts of the same callee).
def shared_far(effect, k):
    if isinstance(effect, Ping):
        yield far_away()
        return (yield Transfer(k, None))
    if isinstance(effect, Pong):
        yield far_away()
        return (yield Transfer(k, None))
    yield Pass(effect, k)
"""

# A pair of handlers that install each other inside their clauses (input for blind case B): A's X clause installs B and yields Y, and B's Y clause installs A and yields Z.
MUTUAL_PY = """\
from doeff import Pass, Transfer, do
from doeff.program import with_handlers

from {pkg}.effects import X, Y, Z


@do
def asks_y():
    yield Y()


@do
def asks_z():
    yield Z()


@do
def asks_x():
    yield X()


def handler_a(effect, k):
    if isinstance(effect, X):
        yield with_handlers([handler_b], asks_y())
        return (yield Transfer(k, None))
    if isinstance(effect, Z):
        return (yield Transfer(k, None))
    yield Pass(effect, k)


def handler_b(effect, k):
    if isinstance(effect, Y):
        yield with_handlers([handler_a], asks_z())
        return (yield Transfer(k, None))
    yield Pass(effect, k)


@do
def warm():
    yield with_handlers([handler_a], asks_x())


@do
def job():
    yield with_handlers([handler_b], asks_y())
"""

# Two Programs of the form `(<- x e :absent F)` (the open_bind that doeff-hy's bind writes).
ABSENT_PY = """\
from doeff import do
from doeff_core_effects._effects import Absent
from doeff_core_effects._outcomes import open_bind


@do
def maybe_one():
    yield Absent("none")


@do
def first():
    return (yield open_bind(maybe_one(), lambda: "first needs one"))


@do
def second():
    return (yield open_bind(maybe_one(), lambda: "second needs one"))
"""

PROGRAMS_PY = """\
from doeff import do
from doeff.program import with_handlers

from {pkg}.effects import Tick
from {pkg}.handlers import two_clauses


@do
def inner():
    yield Tick()


@do
def shared():
    return (yield with_handlers([two_clauses], inner()))


@do
def through_a():
    yield shared()


@do
def through_b():
    yield shared()


@do
def both():
    yield with_handlers([two_clauses], through_a())
    yield with_handlers([two_clauses], through_b())


@do
def x():
    yield Tick()
    yield helper()


@do
def helper():
    return (yield with_handlers([two_clauses], x()))


@do
def top():
    yield x()
    return (yield with_handlers([two_clauses], helper()))
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package(
        {
            "effects.py": EFFECTS_PY,
            "far.py": FAR_PY,
            "handlers.py": HANDLERS_PY,
            "programs.py": PROGRAMS_PY,
            "mutual.py": MUTUAL_PY,
            "absent.py": ABSENT_PY,
        }
    )


def _effect(pkg: str, name: str) -> type:
    """The effect classes of the test package (to build the effect set passed to check_coverage)."""
    return getattr(importlib.import_module(f"{pkg}.effects"), name)


def test_a_clause_is_read_when_an_effect_reaches_it_and_counted_where_it_is_read(pkg: str) -> None:
    handler = reading_sources(lambda: analyze_handler(f"{pkg}.handlers:two_clauses"))
    assert f"{pkg}.handlers" in handler.read
    assert f"{pkg}.far" not in handler.read  # The body of the Ping clause has not been read yet

    pong = reading_sources(lambda: check_coverage([_effect(pkg, "Pong")], [handler.value]))
    assert pong.value.complete
    assert f"{pkg}.far" not in pong.read  # Pong reaches only the Pong clause

    ping = reading_sources(lambda: check_coverage([_effect(pkg, "Ping")], [handler.value]))
    assert f"{pkg}.far" in ping.read  # A source read later is counted by reading_sources at the point where it was pulled
    assert [gap.effect.__name__ for gap in ping.value.gaps] == ["Far"]


def test_a_pickled_handler_keeps_what_its_clauses_emit(pkg: str) -> None:
    handler = analyze_handler(f"{pkg}.handlers:two_clauses")

    copied = pickle.loads(pickle.dumps(handler))

    assert copied == handler
    [ping] = [clause for clause in copied.clauses if clause.handles.__name__ == "Ping"]
    assert [use.effect.__name__ for use in ping.emits.effects] == ["Far"]


def test_a_scope_reached_again_under_the_same_lookups_is_the_report_walked_before(pkg: str) -> None:
    report = analyze_program(f"{pkg}.programs:both")

    under_a, under_b = report.handled
    [shared_a] = under_a.program.handled
    [shared_b] = under_b.program.handled
    assert shared_a.via != shared_b.via  # reached by a different path
    assert shared_a.program is shared_b.program  # does not re-walk the same body
    assert [use.effect.__name__ for use in shared_a.program.effects] == ["Tick"]


def test_a_scope_reached_where_a_call_in_it_is_being_reported_is_walked_again(pkg: str) -> None:
    # helper's scope calls x. When reached from top's handled scope it reads x (Tick), and when reached while x is being reported
    # it does not read x -- a remembered report is not reused where the active set above the seen key differs.
    report = analyze_program(f"{pkg}.programs:top")

    in_scope, under_x = report.handled
    [helper_in_scope] = in_scope.program.handled
    assert helper_in_scope.program.target == under_x.program.target
    assert [use.effect.__name__ for use in helper_in_scope.program.effects] == ["Tick"]
    assert under_x.program.effects == ()


def _gap_names(coverage) -> list[str]:
    """Names of the effects in the answer's gap (for the test's comparison)."""
    return [gap.effect.__name__ for gap in coverage.gaps]


def test_two_threads_asking_two_clauses_of_one_handler_both_read_what_they_emit(make_package) -> None:
    # Blind case A: read a handler in a fresh package, and two threads pull different clauses at the same time. The clauses share a reader, so without a guard one of them
    # walked the other's half-read (empty) facts of far_away and dropped Far (173 of 200 runs on main before the fix).
    rounds = 12
    wrong = []
    for _ in range(rounds):
        pkg = make_package({"effects.py": EFFECTS_PY, "far.py": FAR_PY, "handlers.py": HANDLERS_PY})
        handler = analyze_handler(f"{pkg}.handlers:shared_far")
        barrier = threading.Barrier(2)

        def ask(effect: type, handler=handler, barrier=barrier) -> list[str]:
            barrier.wait()
            return _gap_names(check_coverage([effect], [handler]))

        with ThreadPoolExecutor(max_workers=2) as pool:
            answers = list(pool.map(ask, [_effect(pkg, "Ping"), _effect(pkg, "Pong")]))
        if answers != [["Far"], ["Far"]]:
            wrong.append(answers)
    assert wrong == []


def test_two_analyses_each_count_the_absent_receiver_they_read(pkg: str) -> None:
    # Blind case A: the receiver of `:absent` was remembered once per process, so the second analysis did not re-read that clause and did not count it among the sources read.
    def closure(name: str):
        return _gap_names(check_coverage(analyze_program(f"{pkg}.absent:{name}"), []))

    first = reading_sources(lambda: closure("first"))
    second = reading_sources(lambda: closure("second"))

    assert first.value == second.value == ["Raise"]
    assert "doeff_core_effects._outcomes" in first.read
    assert "doeff_core_effects._outcomes" in second.read


def test_an_analysis_answers_and_reads_the_same_after_other_analyses_in_the_process(make_package) -> None:
    # Blind case B: if handler reads are remembered per process, a handler read in the context of an earlier analysis (the handler being read) was used by a later analysis,
    # and the warm (swapping A -> B -> A) and job answers and the sources read changed depending on which was fired first. Create two fresh packages,
    # fire them in both orders, and compare the answer from firing first with the answer from firing later.
    def closure(pkg: str, name: str):
        def compute():
            coverage = check_coverage(analyze_program(f"{pkg}.mutual:{name}"), [])
            return ([(gap.effect.__name__, gap.origin) for gap in coverage.gaps], coverage.unknown_handlers)

        found = reading_sources(compute)
        return found.value, {module for module in found.read if module.startswith(pkg)}

    files = {"effects.py": EFFECTS_PY, "mutual.py": MUTUAL_PY}
    job_first = make_package(files)
    job_alone, warm_after_job = closure(job_first, "job"), closure(job_first, "warm")
    warm_first = make_package(files)
    warm_alone, job_after_warm = closure(warm_first, "warm"), closure(warm_first, "job")

    assert job_alone == (([], ()), {f"{job_first}.mutual"})
    assert job_after_warm == (([], ()), {f"{warm_first}.mutual"})
    assert warm_alone[0] == warm_after_job[0]
