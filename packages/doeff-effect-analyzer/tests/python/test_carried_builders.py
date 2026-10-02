"""A function an effect carries to build the Program its handler runs (agora-redesign #2973).

``Traverse(f, items)`` carries ``f``: its handler calls ``f(item)`` and runs the Program
it builds where the Traverse was performed (``sequential`` puts the inner handlers and
itself back around it).  ``Traverse`` declares ``f`` in ``__doeff_runs_carried__``, so a
closure check reads ``f``'s body at the performing site, and the handler's
``effect.f(item)`` — rewrapped in handlers before it is yielded — adds nothing where the
handler is read.  Before, the item bodies were read nowhere (a for/do body's effects were
outside every closure check) and the handler's yield was reported as not followed
(``yielded a value that is not a call: prog``).
"""

import pytest
from doeff_effect_analyzer.handler_effects import analyze_handler
from doeff_effect_analyzer.program_effects import analyze_program, runs_where_performed

pytest.importorskip("hy")
pytest.importorskip("doeff_traverse")

EFFECTS_PY = """\
from doeff_vm import EffectBase


class Marked(EffectBase):
    pass


class Build(EffectBase):
    __doeff_runs_carried__ = frozenset({"f"})

    def __init__(self, f, items):
        super().__init__()
        self.f = f
        self.items = items


class Fold(EffectBase):
    __doeff_runs_carried__ = frozenset({"f"})

    def __init__(self, f, init):
        super().__init__()
        self.f = f
        self.init = init


class Elsewhere(EffectBase):
    def __init__(self, f, items):
        super().__init__()
        self.f = f
        self.items = items
"""

# The shape of doeff-traverse's ``sequential``: one local name for the item Program in two
# clauses, rewrapped in handlers (a wrap around a wrap) before it is yielded.
HANDLERS_PY = """\
from doeff import do
from doeff.program import handler as _program_handler
from doeff_vm import Pass, Resume

from {pkg}.effects import Build, Elsewhere, Fold


@do
def _builds(effect, k):
    if isinstance(effect, Build):
        value = None
        for item in effect.items:
            prog = effect.f(item)
            prog = builds(prog)
            prog = builds(builds(prog))
            value = yield prog
        return (yield Resume(k, value))
    if isinstance(effect, Fold):
        prog = effect.f(effect.init)
        prog = builds(prog)
        return (yield Resume(k, (yield prog)))
    yield Pass(effect, k)


builds = _program_handler(_builds)


@do
def _elsewhere(effect, k):
    if isinstance(effect, Elsewhere):
        value = None
        for item in effect.items:
            prog = effect.f(item)
            prog = elsewhere(prog)
            value = yield prog
        return (yield Resume(k, value))
    yield Pass(effect, k)


elsewhere = _program_handler(_elsewhere)
"""

JOBS_PY = """\
from doeff import do

from {pkg}.effects import Build, Elsewhere, Marked


@do
def mark(item):
    yield Marked()
    return item


@do
def local_body_job():
    def step(item):
        yield Marked()
        return item

    return (yield Build(do(step), (1, 2)))


@do
def program_function_job():
    return (yield Build(mark, (1, 2)))


@do
def lambda_job():
    return (yield Build(lambda item: mark(item), (1, 2)))


@do
def undeclared_job():
    def step(item):
        yield Marked()
        return item

    return (yield Elsewhere(do(step), (1, 2)))
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "handlers.py": HANDLERS_PY, "jobs.py": JOBS_PY})


def _short(names) -> set[str]:
    return {name.rsplit(".", 1)[-1] for name in names}


def _folded(pkg: str, job: str) -> set[str]:
    report = analyze_program(f"{pkg}.jobs:{job}")
    assert report.unresolved == (), report.unresolved
    return _short(report.residual_with(runs_where_performed).effect_names)


@pytest.mark.parametrize("job", ["local_body_job", "program_function_job", "lambda_job"])
def test_a_declared_function_field_is_read_where_the_effect_is_performed(
    pkg: str, job: str
) -> None:
    # A do-wrapped local def (what for/do expands to), a Program function, a lambda returning
    # a Program: each is the Program Build's handler runs, read at the performing site.
    assert _folded(pkg, job) == {"Build", "Marked"}


def test_an_undeclared_function_field_is_not_read_as_a_carried_program(pkg: str) -> None:
    # The counter-case: the same shape through an effect that declares nothing.
    assert _folded(pkg, "undeclared_job") == {"Elsewhere"}


def test_a_handler_yielding_a_program_a_declared_field_built_is_followed(pkg: str) -> None:
    # effect.f(item), rewrapped (a wrap around a wrap) and bound on two clause paths, adds
    # nothing in the handler: it was read where the effect was performed.
    for clause in analyze_handler(f"{pkg}.handlers:builds").clauses:
        assert clause.emits.unresolved == (), (clause.handles, clause.emits.unresolved)


def test_a_handler_yielding_a_program_an_undeclared_field_built_stays_unresolved(pkg: str) -> None:
    [clause] = analyze_handler(f"{pkg}.handlers:elsewhere").clauses
    assert [(item.reason, item.text) for item in clause.emits.unresolved] == [
        ("yielded a value that is not a call", "prog")
    ]


def test_doeff_traverse_sequential_is_followed() -> None:
    from doeff_traverse.effects import Traverse
    from doeff_traverse.handlers import sequential

    clauses = [
        c for c in analyze_handler(sequential(), name="sequential").clauses if c.handles is Traverse
    ]
    assert clauses
    assert all(c.emits.unresolved == () for c in clauses), [c.emits.unresolved for c in clauses]
