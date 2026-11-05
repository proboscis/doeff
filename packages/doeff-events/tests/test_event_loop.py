"""Check of the event-loop macro (design = agora-controllers docs/design/event-waits/README.md section 4).

- Result per clause: events are fed to the per-subscriber queue (subscribed_event_handler), each clause body's value becomes the next state, and (stop value) exits.
  A per-event record (slog) remains in the order received.
- Stop signal: if a stop arrives mid-processing, exit at that moment through the stop clause (later events left in the queue are not run). If the stop arrived before the loop,
  exit through the stop clause without running any event.
- Events of types not handled do not arrive: only the types in clause heads are awaited, so events of other types queued in the same subscriber's queue are not passed to a clause.
- Deadline clause: the deadline of ArmTimer reaches a clause as TimerFired.
- Forms rejected at expansion (no stop clause, a clause whose awaited type cannot be derived, etc.) raise HyMacroExpansionError.
"""

import types
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import hy
import pytest
from doeff_core_effects import (
    Listen,
    RaiseStop,
    SlogEffect,
    Spawn,
    listen_handler,
    scheduled,
    scripted_stop_handler,
    slog_discard_handler,
    state,
)
from doeff_events import EventBus, Publish, TimerFired, subscribed_event_handler, timer_handler
from doeff_time import Delay, sim_time_handler

from doeff import do, run, with_handlers

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Added:
    n: int


@dataclass(frozen=True)
class Done:
    pass


@dataclass(frozen=True)
class Unrelated:
    n: int


@dataclass(frozen=True)
class Moved:
    """A signal with a separator in a field name (a field card-key of Hy's defrecord becomes the attribute card_key)."""

    card_key: str


SUBSCRIBED: tuple[type, ...] = (Added, Done, Unrelated, Moved, TimerFired)

PRELUDE = """
(require doeff-hy.macros [defk <-])
(require doeff-events.macros [event-loop])
(import doeff_core_effects._stop_signal_effects [RaiseStop])
(import doeff_events [ArmTimer TimerFired])
(import datetime [datetime])
(import doeff_time [GetTime])
"""

LOOPS = """
(defk folded []
  {:pre [] :post [(: % tuple)]}
  "Accumulate events into state and exit on Done (on the stop signal, add the reason and exit). The form with the state type written."
  (event-loop [seen tuple #()]
    (:stop reason) #(#* seen reason)
    (Added n)      #(#* seen n)
    (Moved :card-key key) #(#* seen key)
    (Done)         (stop seen)))

(defk mistyped []
  {:pre [] :post [(: % tuple)]}
  "A clause that returns str for a state type of tuple — rejected when run."
  (event-loop [seen tuple #()]
    (:stop reason) seen
    (Added n)      "not a tuple"
    (Done)         (stop seen)))

(defk stop-on-first []
  {:pre [] :post [(: % tuple)]}
  "Trigger a stop inside the processing of the first Added — later events left in the queue are not run."
  (event-loop [seen #()]
    (:stop reason) #(#* seen reason)
    (Added n)      (do (when (= n 1) (! (RaiseStop "scene"))) #(#* seen n))
    (Done)         (stop seen)))

(defk doubled-of [n]
  {:pre [(: n int)] :post [(: % int)]}
  "A small Program that the body's <- calls."
  (* n 2))

(defk bound-in-body []
  {:pre [] :post [(: % tuple)]}
  "Bind with <- inside the body's (do ...) — a <- at statement position runs once per event."
  (event-loop [seen #()]
    (:stop reason) #(#* seen reason)
    (Added n)      (do (<- doubled int (doubled-of n))
                       #(#* seen doubled))
    (Done)         (stop seen)))

(defk stateless []
  {:pre [] :post [(: % str)]}
  "The form without state — body values are discarded and it exits on the stop of Done."
  (event-loop
    (:stop reason) reason
    (Added n)      n
    (Done)         (stop "done")))

(defk until-deadline [at]
  {:pre [(: at datetime)] :post [(: % tuple)]}
  "Arm a deadline in the initial value and exit on its TimerFired clause."
  (event-loop [seen (do (! (ArmTimer "end" at)) #())]
    (:stop reason)        #(#* seen reason)
    (Added n)             #(#* seen n)
    (TimerFired :tag tag) (match tag
                            "end" (stop #(#* seen tag))
                            _     seen)))

(defk stopped-when []
  {:pre [] :post [(: % tuple)]}
  "In the stop clause, return the stop reason and the virtual time the stop arrived (to see whether a stop that arrived while waiting takes effect at that time)."
  (event-loop
    (:stop reason) #(reason (! (GetTime)))
    (Added n)      n
    (Done)         (stop #("done" None))))
"""


def _hy_module(source: str) -> types.ModuleType:
    """Load Hy source into a new module (event types are placed on the Python side)."""
    module = types.ModuleType("event_loop_scene")
    vars(module).update({"Added": Added, "Done": Done, "Unrelated": Unrelated, "Moved": Moved})
    hy.eval(hy.read_many(PRELUDE + source), module=module)
    return module


SCENES = _hy_module(LOOPS)


@dataclass(frozen=True)
class Ran:
    """Outcome of a run: value = the value of the Program containing event-loop / events = names of the received event types (in the order of event-loop's slog)."""

    value: object
    events: tuple[str, ...]


@do
def _listened(program: Any) -> Any:
    """Receive, as values, the Program's value and the event-loop slog during it (Listen — collect without rewriting records)."""

    value, logs = yield Listen(program, types=(SlogEffect,))
    events = tuple(str(log.kwargs["event"]) for log in logs if log.msg == "event-loop")
    return Ran(value=value, events=events)


def _run(program: Any) -> Ran:
    """Run on a virtual clock, a one-subscriber queue, deadlines, and a stop signal (in the form raised from a scenario)."""

    bus = EventBus()
    inner = with_handlers([state(), scripted_stop_handler, slog_discard_handler, listen_handler], _listened(program))
    stack = subscribed_event_handler(bus, "loop", SUBSCRIBED)(timer_handler()(inner))
    return run(scheduled(sim_time_handler(start_time=T0)(stack)))


@do
def _published_then(events: tuple[object, ...], program: Any) -> Any:
    """Queue events first and then run the loop (the subscription starts when the handler is built, so none are lost)."""

    for event in events:
        yield Publish(event)
    return (yield program)


def test_each_clause_returns_the_next_state_and_stop_ends_the_loop() -> None:
    ran = _run(_published_then((Added(1), Added(2), Done(), Added(3)), SCENES.folded()))

    assert ran == Ran(value=(1, 2), events=("Added", "Added", "Done"))


def test_a_clause_binds_a_field_whose_name_has_a_separator() -> None:
    # (Moved :card-key key) matches the attribute card_key — unless the field name is converted to the attribute name it falls to the _ branch and raises TypeError.
    ran = _run(_published_then((Moved("ki-1"), Added(2), Done()), SCENES.folded()))

    assert ran == Ran(value=("ki-1", 2), events=("Moved", "Added", "Done"))


def test_a_clause_value_of_another_type_than_the_state_is_refused() -> None:
    with pytest.raises(AssertionError, match="tuple"):
        _run(_published_then((Added(1), Done()), SCENES.mistyped()))


def test_events_of_types_no_clause_names_never_reach_a_clause() -> None:
    ran = _run(_published_then((Unrelated(9), Added(1), Unrelated(8), Done()), SCENES.folded()))

    assert ran == Ran(value=(1,), events=("Added", "Done"))


def test_a_stop_raised_while_processing_ends_the_loop_before_the_queued_events() -> None:
    ran = _run(_published_then((Added(1), Added(2), Done()), SCENES.stop_on_first()))

    assert ran == Ran(value=(1, "scene"), events=("Added",))


def test_a_stop_requested_before_the_loop_runs_no_clause() -> None:
    @do
    def stopped_first() -> Any:
        yield RaiseStop("before")
        return (yield _published_then((Added(1), Done()), SCENES.folded()))

    assert _run(stopped_first()) == Ran(value=("before",), events=())


def test_a_bind_in_a_clause_body_runs_for_each_event() -> None:
    ran = _run(_published_then((Added(1), Added(2), Done()), SCENES.bound_in_body()))

    assert ran.value == (2, 4)


def test_the_stateless_form_ends_with_stop() -> None:
    ran = _run(_published_then((Added(1), Added(2), Done()), SCENES.stateless()))

    assert ran == Ran(value="done", events=("Added", "Added", "Done"))


def test_a_deadline_armed_in_the_initial_state_reaches_the_timer_clause() -> None:
    ran = _run(_published_then((Added(1),), SCENES.until_deadline(T0 + timedelta(hours=6))))

    assert ran == Ran(value=(1, "end"), events=("Added", "TimerFired"))


@do
def _stop_raised_after(seconds: float, reason: str) -> Any:
    """To trigger a stop after a virtual seconds seconds (a task outside the loop — stands in for the production signal receiver)."""

    yield Delay(seconds)
    yield RaiseStop(reason)


@do
def _stopped_later(seconds: float, reason: str, program: Any) -> Any:
    """To spawn a task that triggers a stop at a later time and then run a loop waiting on a queue where nothing arrives."""

    yield Spawn(_stop_raised_after(seconds, reason))
    return (yield program)


def test_a_stop_that_comes_while_the_loop_waits_ends_it_at_that_moment() -> None:
    # A stop that arrives while waiting exits through the stop clause at that time (it does not wait for the next event or deadline).
    ran = _run(_stopped_later(60.0, "later", SCENES.stopped_when()))

    assert ran == Ran(value=("later", T0 + timedelta(seconds=60)), events=())


@do
def _spawns_during(program: Any) -> Any:
    """To read the Program's value and the number of Spawns the Program issued during it (Listen — collect the issued effects without rewriting them)."""

    value, spawns = yield Listen(program, types=(Spawn,))
    return (value, len(spawns))


def _spawned_by_folding(count: int) -> Any:
    """To read the folded value after feeding count Added and Done, and the number of Spawns during it.

    So as not to nest with _run's Listen (which collects slog), build the same assembly with only a Listen that counts Spawns.
    """

    events = (*(Added(n) for n in range(1, count + 1)), Done())
    bus = EventBus()
    inner = with_handlers(
        [state(), scripted_stop_handler, slog_discard_handler, listen_handler],
        _spawns_during(_published_then(events, SCENES.folded())),
    )
    stack = subscribed_event_handler(bus, "loop", SUBSCRIBED)(timer_handler()(inner))
    return run(scheduled(sim_time_handler(start_time=T0)(stack)))


def test_waking_for_each_event_spawns_no_task() -> None:
    # Spawns are only the number fixed by the lifetime (the stop watcher and its cleanup at the end) and do not grow with the number of received events. In the form that
    # makes each wait a task on every wake-up and races it with the stop, it grows by one per extra event (this check goes red).
    one = _spawned_by_folding(1)
    five = _spawned_by_folding(5)

    assert one[0] == (1,) and five[0] == (1, 2, 3, 4, 5)
    assert one[1] == five[1], (one, five)


@pytest.mark.parametrize(
    ("loop", "message"),
    [
        ("(event-loop [s 0] (Added n) n)", "stop clause"),
        ("(event-loop [s 0] (:stop r) r (:stop q) q (Added n) n)", "exactly one stop clause"),
        ("(event-loop [s 0] (:stop r) r)", "no awaited-type clause"),
        ("(event-loop [s 0] (:stop r) r _ s)", "cannot derive the awaited type"),
        ("(event-loop [s 0] (:stop r) r (_ n) s)", "cannot derive the awaited type"),
        ("(event-loop [s 0] (:stop r) r ev s)", "cannot derive the awaited type"),
        ("(event-loop [s 0] (:stop r) r (Added n) n (Added m) m)", "two clauses of the same type"),
        ("(event-loop [s 0] (:stop r) r (Added n))", "without a body"),
        ("(event-loop [s 0] (:stop r) r (Added 1) s)", "do not narrow by value"),
        ("(event-loop [s 0] (:stop r) r (Added n) (do (stop n) n))", "last-value position"),
        ("(event-loop [s] (:stop r) r (Added n) n)", "\\[name init\\]"),
    ],
)
def test_malformed_loops_are_refused_when_expanded(loop: str, message: str) -> None:
    source = f'(defk broken [] {{:pre [] :post [(: % int)]}} "broken form" {loop})'
    with pytest.raises(hy.errors.HyMacroExpansionError, match=message):
        _hy_module(source)
