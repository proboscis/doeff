"""A handler a clause of its own installs again is that same handler.

doeff-traverse's ``sequential`` reports each item's outcome under itself: its Traverse clause
calls ``_report(_sequential_handler, …)``, which yields ``WithHandlerType(handler, …)``.  The
reader binds ``handler`` to the handler being read, and read it as unknown there ("handler
installs itself inside its own clause"): every closure check whose foundation holds
``sequential`` named ``handler`` in ``unknown_handlers``.  The handler installed again is read
as the same handler — its clauses — and what leaves a clause that is reached again inside
what it performs is worked out until nothing more leaves (the least answer: a handler between
the reinstall and the clause can answer what the inner turn performs).
"""

import pytest
from doeff_effect_analyzer._handler_effects import analyze_handler

pytest.importorskip("hy")

EFFECTS_PY = """\
from doeff import EffectBase


class Count(EffectBase):
    pass


class Report(EffectBase):
    pass


class Again(EffectBase):
    pass


class Leaf(EffectBase):
    pass


class Deep(EffectBase):
    pass


class Ping(EffectBase):
    pass


class Pong(EffectBase):
    pass
"""

HANDLERS_PY = """\
from doeff import Pass, Resume, do
from doeff.program import WithHandlerType

from {pkg}.effects import Again, Count, Deep, Leaf, Ping, Pong, Report


@do
def _under(handler, report):
    # The shape of doeff-traverse's _report: the handler comes in as an argument.
    return (yield WithHandlerType(handler, report))


@do
def _counts(effect, k):
    if isinstance(effect, Count):
        yield _under(_counts, Report())
        return (yield Resume(k, None))
    if isinstance(effect, Report):
        return (yield Resume(k, None))
    yield Pass(effect, k)


@do
def _deepens(effect, k):
    if isinstance(effect, Leaf):
        yield Deep()
        return (yield Resume(k, None))
    yield Pass(effect, k)


@do
def _nests(effect, k):
    # Inside, Again reaches this clause again; the Leaf that turn performs meets _deepens.
    if isinstance(effect, Again):
        yield Leaf()
        yield WithHandlerType(_deepens, WithHandlerType(_nests, Again()))
        return (yield Resume(k, None))
    yield Pass(effect, k)


@do
def _pings(effect, k):
    if isinstance(effect, Ping):
        yield _under(_pongs, Pong())
        return (yield Resume(k, None))
    yield Pass(effect, k)


@do
def _pongs(effect, k):
    if isinstance(effect, Pong):
        yield _under(_pings, Ping())
        return (yield Resume(k, None))
    yield Pass(effect, k)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "handlers.py": HANDLERS_PY})


def _left(pkg: str, handler: str, effect: str) -> tuple[set[str], tuple[str, ...]]:
    """What leaves ``handler``'s clause for ``effect``, and the handlers on the way it could not read."""
    [clause] = [
        c for c in analyze_handler(f"{pkg}.handlers:{handler}").clauses if c.handles.__name__ == effect
    ]
    left = clause.emits.residual
    return {escape.effect.__name__ for escape in left.escapes}, left.unknown_handlers


def test_a_handler_passed_to_a_helper_that_installs_it_is_the_handler_itself(pkg: str) -> None:
    # Before: ({"Report"}, ("handler",)) — the handler installed again answered nothing.
    assert _left(pkg, "_counts", "Count") == (set(), ())


def test_what_the_inner_turn_performs_meets_the_handlers_between_it_and_the_clause(pkg: str) -> None:
    # Again answered inside performs Leaf there, which _deepens answers with Deep. Taking the inner
    # turn to perform nothing would leave {"Leaf"} and hide Deep. Before: ({"Again", "Leaf"}, ("_nests",)).
    assert _left(pkg, "_nests", "Again") == ({"Leaf", "Deep"}, ())


def test_two_handlers_that_install_each_other_are_read_to_an_end(pkg: str) -> None:
    # Ping → Pong under _pongs → Ping under _pings → …: nothing else is performed, so nothing leaves.
    # Before: ({"Ping"}, ("handler",)).
    assert _left(pkg, "_pings", "Ping") == (set(), ())
    assert _left(pkg, "_pongs", "Pong") == (set(), ())


def test_doeff_traverse_sequential_reports_under_a_handler_it_reads() -> None:
    """``sequential`` reports each item's outcome under itself (``_report``): no clause names a
    handler it could not read — a closure check of a foundation holding it named ``handler`` in
    ``unknown_handlers``."""
    pytest.importorskip("doeff_traverse")
    from doeff_traverse import sequential

    read = analyze_handler(sequential, name="sequential")
    assert [
        (c.handles.__name__, c.emits.residual.unknown_handlers)
        for c in read.clauses
        if c.emits.residual.unknown_handlers
    ] == []
