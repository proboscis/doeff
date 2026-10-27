"""Handlers carried as a value, read through the type of the place they come from (doeff's ``BuiltBy``).

Regression target (card ki-e912b1821351): agora-controllers' screen job places the notice receivers it
gets in the answer of an effect (``(with-handlers [#* receivers] …)``) and its CLI host places the
records clients a setting answers (``(with-handlers (list lane.handlers) …)``). The reader could not
know the values and showed ``receivers`` / ``list(lane.handlers)`` as unread handlers, so the closure
checks stayed open. The type of the place now names the factories (``Annotated[Callable, BuiltBy(f)]``):
the reader reads the list as the handlers ``f`` makes — what they answer, and what they perform outward.
"""

import importlib

import pytest
from doeff_effect_analyzer.handler_effects import analyze_handler, check_coverage
from doeff_effect_analyzer.program_effects import analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff import EffectBase


@dataclass(frozen=True)
class Ping(EffectBase):
    pass


@dataclass(frozen=True)
class Subscribe(EffectBase):
    pass


@dataclass(frozen=True)
class OpenA(EffectBase):
    handlers: tuple


@dataclass(frozen=True)
class OpenB(EffectBase):
    handlers: tuple
"""

# A factory the reader knows by its declaration (the shape of doeff-events' notice_events_handler):
# it answers Ping and performs Subscribe around the body.
FACTORY_PY = """\
from {pkg}.effects import Ping, Subscribe


def receiver(source):
    raise NotImplementedError


receiver.__doeff_handles__ = (Ping,)
receiver.__doeff_effects__ = (Subscribe,)
"""

PLACES_HY = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import typing [Annotated])
(import doeff [with-handlers])
(import doeff.handler_values [BuiltBy])
(import {pkg}.effects [Ping])
(import {pkg}.factory [receiver])

(setv Receiver (get Annotated #((get Callable #(... object)) (BuiltBy receiver))))
(setv Plain (get Callable #(... object)))

(defrecord Lane
  "A record carrying receivers."
  (#^ (get tuple #(Receiver ...)) handlers))

(defk under-receivers [receivers]
  {:pre [(: receivers (get tuple #(Receiver ...)))] :post [(: % "None")]}
  (<- answer (with-handlers [#* receivers] (Ping)))
  answer)

(defk in-lane [lane]
  {:pre [(: lane Lane)] :post [(: % "None")]}
  (<- answer (with-handlers (list lane.handlers) (Ping)))
  answer)

(defk under-plain [handlers]
  {:pre [(: handlers (get tuple #(Plain ...)))] :post [(: % "None")]}
  (<- answer (with-handlers [#* handlers] (Ping)))
  answer)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "factory.py": FACTORY_PY, "places.hy": PLACES_HY})


def _gaps(coverage) -> set[str]:
    return {gap.effect.__name__ for gap in coverage.gaps}


def test_a_parameter_typed_with_built_by_reads_as_the_factory(pkg: str) -> None:
    coverage = check_coverage(analyze_program(f"{pkg}.places:under_receivers"), [])

    assert coverage.unknown_handlers == ()
    assert _gaps(coverage) == {"Subscribe"}


def test_a_record_field_typed_with_built_by_reads_as_the_factory(pkg: str) -> None:
    coverage = check_coverage(analyze_program(f"{pkg}.places:in_lane"), [])

    assert coverage.unknown_handlers == ()
    assert _gaps(coverage) == {"Subscribe"}


def test_a_carried_list_without_built_by_stays_unread(pkg: str) -> None:
    # The counter-case: a plain Callable type says nothing of what the value answers.
    coverage = check_coverage(analyze_program(f"{pkg}.places:under_plain"), [])

    assert coverage.unknown_handlers != ()


# Two clauses of one handler bind a field of the same name (``handlers``) of effects whose field types name
# the factory: doeff-hy's clauses share one function, so the name is a local assigned on several paths.
CLAUSES_HY = """\
(require doeff-hy.macros [defhandler <-])
(import collections.abc [Callable])
(import typing [Annotated])
(import doeff [with-handlers])
(import doeff.handler_values [BuiltBy])
(import {pkg}.effects [Ping OpenA OpenB])
(import {pkg}.factory [receiver])

(setv Receiver (get Annotated #((get Callable #(... object)) (BuiltBy receiver))))
(setv OpenA.__annotations__ {"handlers" (get tuple #(Receiver ...))})
(setv OpenB.__annotations__ {"handlers" (get tuple #(Receiver ...))})

(defhandler opening
  (OpenA [handlers]
    (<- answer (with-handlers (list handlers) (Ping)))
    (resume answer))
  (OpenB [handlers]
    (<- answer (with-handlers [#* handlers] (Ping)))
    (resume answer)))
"""


@pytest.fixture
def clauses_pkg(make_package) -> str:
    return make_package({"effects.py": EFFECTS_PY, "factory.py": FACTORY_PY, "clauses.hy": CLAUSES_HY})


def test_a_field_bound_in_several_clauses_reads_as_the_factory(clauses_pkg: str) -> None:
    handler = analyze_handler(importlib.import_module(f"{clauses_pkg}.clauses").opening)

    for clause in handler.clauses:
        assert clause.emits.residual.unknown_handlers == (), clause.handles
        assert {name.rsplit(".", 1)[-1] for name in clause.emits.effect_names} == {"Subscribe"}, clause.handles
