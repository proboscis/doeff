"""A handler that answers every effect and performs nothing seals the Program it is placed over.

Regression target: doeff-stream's ``step_answer`` runs the step's Program (a value made at run time,
which the reader cannot follow) under ``_refuse_every_effect``, a handler without an ``isinstance``
clause. Every closure check of a Program that folds a stream (agora-controllers' kanban watch loop)
showed ``_refuse_every_effect`` as an unread handler and ``yielded a value that is not a call: answer``
as an unfollowed place — though nothing the step's Program performs can leave that handler.
Declared ``__doeff_handles__ = (EffectBase,)`` and ``__doeff_effects__ = ()``, the handler is read
as one that every effect reaches and nothing leaves: what is inside it is not named.
"""

import pytest
from doeff_effect_analyzer._program_effects import analyze_program

pytest.importorskip("doeff")

EFFECTS_PY = """\
from doeff import EffectBase


class Tick(EffectBase):
    pass
"""

PROGRAMS_PY = """\
from doeff import EffectBase, K, do, handler

from {pkg}.effects import Tick


@handler
def refuse(effect: object, k: K):
    return ("refused", effect)


refuse.__doeff_handles__ = (EffectBase,)
refuse.__doeff_effects__ = ()


@handler
def undeclared_refuse(effect: object, k: K):
    return ("refused", effect)


@handler
def ticking_refuse(effect: object, k: K):
    yield Tick()
    return ("refused", effect)


ticking_refuse.__doeff_handles__ = (EffectBase,)
ticking_refuse.__doeff_effects__ = (Tick,)


@do
def sealed(answer):
    return (yield refuse(answer))


@do
def sealed_then_ticks(answer):
    value = yield refuse(answer)
    yield Tick()
    return value


@do
def undeclared(answer):
    return (yield undeclared_refuse(answer))


@do
def ticking(answer):
    return (yield ticking_refuse(answer))
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "programs.py": PROGRAMS_PY})


def _short(names: list[str]) -> set[str]:
    return {name.rsplit(".", 1)[-1] for name in names}


def test_a_declared_refusing_handler_seals_the_program_it_wraps(pkg: str) -> None:
    left = analyze_program(f"{pkg}.programs:sealed").residual

    assert left.escapes == ()
    assert left.unknown_handlers == ()
    assert left.unresolved == ()


def test_what_is_performed_outside_the_seal_still_leaves(pkg: str) -> None:
    left = analyze_program(f"{pkg}.programs:sealed_then_ticks").residual

    assert _short(left.effect_names) == {"Tick"}
    assert left.unresolved == ()


def test_an_undeclared_refusing_handler_leaves_its_inside_named(pkg: str) -> None:
    # The counter-case: without the declaration the handler is unread, and the Program it wraps
    # stays an unfollowed place.
    left = analyze_program(f"{pkg}.programs:undeclared").residual

    assert left.unknown_handlers == ("undeclared_refuse",)
    assert [u.text for u in left.unresolved] == ["answer"]


def test_a_handler_that_performs_when_it_answers_does_not_seal(pkg: str) -> None:
    # The counter-case: an effect the unfollowed Program performs is answered with Tick, which
    # leaves — the place stays named, for what it performs is not known to end there.
    left = analyze_program(f"{pkg}.programs:ticking").residual

    assert [u.text for u in left.unresolved] == ["answer"]
