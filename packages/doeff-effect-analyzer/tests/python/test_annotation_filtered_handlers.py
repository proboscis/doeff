"""A handler value whose raw ``(effect, k)`` dispatches by its effect parameter's annotation alone.

Regression target (card ki-36f06eb1c65a): doeff-events' ``broker_patience_handler`` builds
``handler(effect: ReadBrokerPatience, k)`` and installs it with ``doeff.handler``; agora-controllers
keeps the value (``(val broker-stall-patience (broker-patience-handler …))``) and places it by name in
its foundation. The VM gives the raw handler only the effects of the annotated class (as if it began
with ``if not isinstance(effect, T): yield Pass(effect, k)``), but the reader looked for an
``isinstance`` test only, and the declaration on the factory does not reach the value: every closure
check of a production foundation showed ``unknown=('broker_stall_patience',)``.
"""

import pytest
from doeff_effect_analyzer.handler_effects import analyze_handler

pytest.importorskip("doeff_vm")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff import EffectBase


@dataclass(frozen=True)
class ReadPatience(EffectBase):
    pass


@dataclass(frozen=True)
class ReadDeadline(EffectBase):
    pass


@dataclass(frozen=True)
class Nap(EffectBase):
    seconds: float
"""

HANDLERS_PY = """\
from doeff import handler
from doeff import EffectBase, Resume

from {pkg}.effects import Nap, ReadDeadline, ReadPatience


def patience_handler(seconds):
    @handler
    def answer(effect: ReadPatience, k):
        yield Nap(0.0)
        return (yield Resume(k, seconds))

    return answer


def either_handler(seconds):
    @handler
    def answer(effect: ReadPatience | ReadDeadline, k):
        return (yield Resume(k, seconds))

    return answer


def open_handler(seconds):
    @handler
    def answer(effect: EffectBase, k):
        return (yield Resume(k, seconds))

    return answer


PATIENCE = patience_handler(900.0)
EITHER = either_handler(900.0)
OPEN = open_handler(900.0)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "handlers.py": HANDLERS_PY})


def _short(classes) -> set[str]:
    return {cls.__name__ for cls in classes}


def test_a_handler_value_is_read_by_its_effect_annotation(pkg: str) -> None:
    read = analyze_handler(f"{pkg}.handlers:PATIENCE")

    assert read.known, read.unresolved
    assert _short(read.handled) == {"ReadPatience"}
    [clause] = read.clauses
    assert {name.rsplit(".", 1)[-1] for name in clause.emits.effect_names} == {"Nap"}


def test_a_union_annotation_is_a_clause_for_each_member(pkg: str) -> None:
    read = analyze_handler(f"{pkg}.handlers:EITHER")

    assert _short(read.handled) == {"ReadPatience", "ReadDeadline"}


def test_an_annotation_that_filters_nothing_stays_unread(pkg: str) -> None:
    # The counter-case: ``effect: EffectBase`` gives the handler every effect (the VM sets no
    # filter), so what it answers is not written in the annotation.
    read = analyze_handler(f"{pkg}.handlers:OPEN")

    assert not read.known
