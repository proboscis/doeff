"""Whether doeff-effect-analyzer can read ``redis_notice_handler`` and ``broker_back_by_retry`` when a job places them
in its handler list.

A job writes each factory as a call in its ``with_handlers`` list. The analyzer cannot read inside the factories (the
handler is made inside the function), so each factory declares what it answers and what it performs while answering
(``__doeff_handles__`` and ``__doeff_effects__``). Without the declaration a closure check of the job reports an
unreadable handler, and a user had to copy the answered effects by hand — a copy that did not follow when the Redis
handler began to answer ``AckAnnouncement`` (card ki-c3b888f955e2).

- The analyzer reads each declaration.
- The declared answers are what the real handler answers: the clauses the analyzer reads and the annotation the VM
  filters by (an effect added to the handler and not to the declaration fails here).
- The declared effects are what the real handler performs while answering (and, for the Redis handler, around the
  body).
- A job that places the three notice factories leaves no unreadable handler and no notice effect unanswered.
"""

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from doeff_core_effects.effects import Await
from doeff_events import (
    MarkGap,
    NoticeRoute,
    broker_back_by_retry,
    notice_events_handler,
    redis_notice_handler,
)
from doeff_events.effects.notices import Announce, AwaitBrokerBack, ProbeBroker
from doeff_events.handlers.redis_notices import (
    BROKER_BACK_ANSWERS,
    BROKER_BACK_EFFECTS,
    REDIS_NOTICE_ANSWERS,
    REDIS_NOTICE_EFFECTS,
    _broker_handler,
    _client,
    _run,
)
from doeff_vm._effect_types import handler_effect_types

from doeff import EffectGenerator, do, with_handlers

# The Python front end of doeff-effect-analyzer is a development tool, not a runtime dependency of doeff-events
# (same treatment as test_notice_events_closure.py).
if importlib.util.find_spec("doeff_effect_analyzer") is None:
    sys.path.insert(
        0, str(Path(__file__).resolve().parents[2] / "doeff-effect-analyzer" / "python")
    )

from doeff_effect_analyzer.handler_effects import (
    Basis,
    analyze_handler,
    check_coverage,
)
from doeff_effect_analyzer.program_effects import (
    analyze_program,
    runs_where_performed,
)

NOTICES_MODULE = "doeff_events.effects.notices"


@dataclass(frozen=True)
class Said:
    """The event the job's sender routes through the broker."""

    text: str


def _rooms(event: Said) -> str:
    return "rooms"


def _encoded(event: Said) -> str:
    return event.text


def _decoded(text: str) -> Said:
    return Said(text=text)


ROUTES = (
    NoticeRoute(
        event_type=Said,
        wire_name="said",
        channel=_rooms,
        encode=_encoded,
        decode=_decoded,
        when_unsent=MarkGap(),
        reads=("rooms",),
    ),
)


@do
def _announces() -> EffectGenerator[object]:
    """The body: announce one notice (answered by the Redis handler) and answer what it answered."""
    answer: object = yield Announce(channel="rooms", name="said", body="hello")
    return answer


@do
def _wrapped() -> EffectGenerator[object]:
    """The Redis wrapper running the body ``_announces`` — the target for reading what it performs around the body."""
    answer: object = yield _run("redis://broker:6379/0", 1.0, 7.0, 30.0, _announces())
    return answer


@do
def _job() -> EffectGenerator[object]:
    """The shape a job uses: the three factories written as calls in its handler list, outermost first."""
    answer: object = yield with_handlers(
        [
            redis_notice_handler("redis://broker:6379/0", 1.0),
            broker_back_by_retry(1.0),
            notice_events_handler("rooms-source", ROUTES, 30.0),
        ],
        _announces(),
    )
    return answer


def _raw(installed: object) -> Callable[..., object]:
    """The handler function the VM calls, wrapped by a Program -> Program handler (the field doeff.program.handler sets)."""
    return installed.__doeff_handler_data__  # type: ignore[attr-defined] — the field doeff.program.handler sets (untyped)


def _real_broker_handler() -> object:
    """The handler ``_run`` installs around the body, on clients that connect to nothing until a call is made."""
    sending = _client("redis://broker:6379/0", 1, 1.0, calls_end=True)
    receiving = _client("redis://broker:6379/0", 0, 1.0, calls_end=False)
    return _broker_handler(sending, receiving, 7.0, 30.0)


def _emitted(handler: object) -> frozenset[type]:
    """Every effect the clauses of ``handler`` perform, as the analyzer reads them."""
    read = analyze_handler(handler)
    assert read.basis is Basis.CLAUSES, read.unresolved
    return frozenset(effect for clause in read.clauses for effect in clause.emits.effect_types)


def test_the_analyzer_reads_the_redis_handler_from_its_declaration() -> None:
    handler = analyze_handler("doeff_events.handlers.redis_notices:redis_notice_handler")
    assert handler.basis is Basis.DECLARED, handler.unresolved
    assert handler.handled == frozenset(REDIS_NOTICE_ANSWERS)
    for clause in handler.clauses:
        assert clause.emits.effect_types == frozenset(REDIS_NOTICE_EFFECTS)


def test_the_redis_handler_answers_what_it_declares() -> None:
    # The clauses read from source and the annotation the VM skips the handler by: an effect answered by one and not
    # declared (or declared and answered by neither) fails here, in the commit that adds it.
    real = _real_broker_handler()
    assert analyze_handler(real).handled == frozenset(REDIS_NOTICE_ANSWERS)
    assert handler_effect_types(_raw(real)) == REDIS_NOTICE_ANSWERS


def test_the_redis_handler_performs_what_it_declares() -> None:
    real = _real_broker_handler()
    around = analyze_program(_wrapped).residual_with(runs_where_performed).effect_types
    body = analyze_program(_announces).residual_with(runs_where_performed).effect_types
    assert _emitted(real) | (around - body) == frozenset(REDIS_NOTICE_EFFECTS)


def test_the_analyzer_reads_the_retry_handler_from_its_declaration() -> None:
    handler = analyze_handler("doeff_events.handlers.redis_notices:broker_back_by_retry")
    assert handler.basis is Basis.DECLARED, handler.unresolved
    assert handler.handled == frozenset(BROKER_BACK_ANSWERS) == frozenset({AwaitBrokerBack})
    for clause in handler.clauses:
        assert clause.emits.effect_types == frozenset(BROKER_BACK_EFFECTS)


def test_the_retry_handler_answers_and_performs_what_it_declares() -> None:
    # The handler is selected by its annotation (no isinstance clause to read), so its answer is the VM's filter, and
    # what it performs is read from its body.
    real = _raw(broker_back_by_retry(1.0))
    assert handler_effect_types(real) == BROKER_BACK_ANSWERS
    assert analyze_program(real).effect_types == frozenset(BROKER_BACK_EFFECTS)
    assert ProbeBroker in BROKER_BACK_EFFECTS


def test_a_job_placing_the_notice_factories_leaves_no_unreadable_handler_and_no_notice_gap() -> (
    None
):
    scheduler = analyze_handler("doeff_core_effects.scheduler:scheduled", name="scheduled")
    coverage = check_coverage(analyze_program(_job), [scheduler], include=runs_where_performed)
    assert coverage.unknown_handlers == (), coverage.unknown_handlers
    unanswered = tuple(gap for gap in coverage.gaps if gap.effect.__module__ == NOTICES_MODULE)
    assert unanswered == (), unanswered
    # What leaves is the clients' calls and the clock's effects, for the handlers outside the job.
    assert Await in {gap.effect for gap in coverage.gaps}
