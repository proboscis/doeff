"""Whether doeff-effect-analyzer can read ``notice_events_handler`` when a job places it in its handler list.

A job writes the factory as a call in its ``with_handlers`` list. The analyzer cannot read inside the factory, so
the factory declares what it closes and what it performs (``__doeff_handles__ = ()`` and
``__doeff_effects__ = NOTICE_EVENTS_EFFECTS``). Without the declaration a closure check of the job reports an
unreadable handler (agora-redesign #3850 — agora-controllers listed it as known-unreadable until this landed).

- The analyzer reads the declaration: the wrapper closes nothing and performs ``NOTICE_EVENTS_EFFECTS``.
- The declared effects are what the wrapper performs around a body (changing either side fails).
- A job that places the factory leaves no unreadable handler.
"""

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

from doeff_events import EventBus, NoticeRoute, notice_events_handler, subscribed_event_handler
from doeff_events.effects import WaitForEvent
from doeff_events.effects.notices import Announce
from doeff_events.handlers.notice_events import NOTICE_EVENTS_EFFECTS, _checked_plan, _run

from doeff import EffectGenerator, do, with_handlers

# The Python front end of doeff-effect-analyzer is a development tool, not a runtime dependency of doeff-events
# (same treatment as doeff-records' closure test).
if importlib.util.find_spec("doeff_effect_analyzer") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "doeff-effect-analyzer" / "python"))

from doeff_effect_analyzer.handler_effects import Basis, analyze_handler, check_coverage  # noqa: E402 - after sys.path
from doeff_effect_analyzer.program_effects import analyze_program, runs_where_performed  # noqa: E402 - after sys.path


@dataclass(frozen=True)
class Rang:
    """The event the test routes through the broker."""

    room: str


def _channel(event: Rang) -> str:
    return "rooms"


def _encoded(event: Rang) -> str:
    return event.room


def _decoded(text: str) -> Rang:
    return Rang(room=text)


ROUTES = (
    NoticeRoute(
        event_type=Rang,
        wire_name="rang",
        channel=_channel,
        encode=_encoded,
        decode=_decoded,
        held_key=_encoded,
        reads=("rooms",),
    ),
)
BUS = EventBus()


@do
def _waits() -> EffectGenerator[Rang]:
    """The body: wait for one event and answer it."""
    came: Rang = yield WaitForEvent(Rang)
    return came


@do
def _wrapped() -> EffectGenerator[Rang]:
    """The wrapper running the body ``_waits`` — the target for reading what it performs around the body."""
    answer: Rang = yield _run(_checked_plan("rooms-source", ROUTES, 30.0), _waits())
    return answer


@do
def _job() -> EffectGenerator[Rang]:
    """The shape a job uses: the factory written as a call inside the subscriber queue's handler."""
    answer: Rang = yield with_handlers(
        [subscribed_event_handler(BUS, "listener", (Rang,)), notice_events_handler("rooms-source", ROUTES, 30.0)],
        _waits(),
    )
    return answer


def test_the_analyzer_reads_the_factory_from_its_declaration() -> None:
    handler = analyze_handler("doeff_events.handlers.notice_events:notice_events_handler")
    assert handler.basis is Basis.DECLARED, handler.unresolved
    assert handler.handled == frozenset()
    assert handler.performs is not None
    assert handler.performs.effect_types == frozenset(NOTICE_EVENTS_EFFECTS)


def test_the_declared_effects_are_what_the_wrapper_performs_around_the_body() -> None:
    declared = analyze_handler("doeff_events.handlers.notice_events:notice_events_handler").performs
    assert declared is not None
    around = analyze_program(_wrapped).residual_with(runs_where_performed).effect_types
    body = analyze_program(_waits).residual_with(runs_where_performed).effect_types
    # ``Announce`` is performed inside the clause that sends a routed ``Publish`` (``_sent``), which the reading of
    # the run around the body does not enter — it is declared, and counted here by name.
    assert Announce not in around
    assert declared.effect_types == (around - body) | {Announce}


def test_a_job_placing_the_factory_leaves_no_unreadable_handler() -> None:
    scheduler = analyze_handler("doeff_core_effects.scheduler:scheduled", name="scheduled")
    coverage = check_coverage(analyze_program(_job), [scheduler], include=runs_where_performed)
    assert coverage.unknown_handlers == (), coverage.unknown_handlers
