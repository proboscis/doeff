"""A cancel that lands while the answer of a ``Spawn`` is still queued leaves no task of the wrapper behind.

The scheduler drops the answer of a ``Spawn`` (the task's handle) when the spawner is cancelled before it resumes, so
the spawner can neither learn nor stop the task it just made (the first such window was the source task of
``_run``, card ki-f01c529b539a). The wrapper has three more tasks that wait for the broker's return — the watcher of
``_came_back_within``, and the gap waiter that ``_marked`` and ``_started_gaps_told`` make — and a task parked in
``AwaitBrokerBack`` cannot be woken by anything but its cancel. So each of them must not begin to wait before its
spawner holds its handle.

Each law makes the window on purpose: a canceller of higher priority is woken by the spawner's own ``Spawn`` (the
layer under the wrapper wakes it there), so it runs before the spawner's resume. After the spawner is gone the laws
count the tasks still parked in ``AwaitBrokerBack`` (a layer under the wrapper counts them: it answers the wait
itself, and counts down when the wait ends by an answer or by a cancel).

No ``from __future__ import annotations`` here: the VM reads a handler's effect types from the annotation of its
first parameter.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Final, final

from doeff_core_effects import (
    PRIORITY_HIGH,
    PRIORITY_NORMAL,
    Cancel,
    CompletePromise,
    CreatePromise,
    Promise,
    Spawn,
    Task,
    TaskCancelledError,
    Wait,
)
from doeff_events import EventBus, subscribed_event_handler
from doeff_events.effects.events import Publish
from doeff_events.effects.notices import AwaitBrokerBack
from doeff_events.handlers.memory_notices import MemoryBroker, cut_broker, memory_notice_handler
from doeff_events.handlers.notice_events import MarkGap, NoticeRoute
from doeff_events.notice_laws import LawNote, law_routes
from doeff_time import Delay
from events_test_support import patient_notice_events
from notice_law_support import (
    PATIENCE_SECONDS,
    PREFIX,
    SenderOutage,
    refuses_senders,
    run_on_virtual_clock,
)

from doeff import K, Pass, Program, Pure, Resume, do
from doeff import handler as program_handler

if TYPE_CHECKING:
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler

CHANNEL: Final = f"{PREFIX}:note"

Layer = Callable[[Program[object]], Program[object]]
"""A handler, as a function from a program to the program under it."""


@final
class _Parked:
    """How many tasks wait in ``AwaitBrokerBack`` right now, and how many did once the party was gone (read inside the
    run: the scheduler's close-out at the run's end cancels what is left, which counts it out)."""

    __slots__ = ("after_the_party", "waiting")

    def __init__(self) -> None:
        """Start with nobody waiting."""
        self.waiting = 0
        self.after_the_party = 0


def _counts_the_waits_for_the_return(parked: _Parked) -> "ProgramHandler":
    """The layer under the wrapper that answers the wait for the broker's return itself (asking the broker below it)
    and counts the tasks that are inside it."""

    @do
    def handler(effect: AwaitBrokerBack, k: K) -> "EffectGenerator[object]":
        """Count the waiter in, ask the layer below, count it out when the wait ends — also by a cancel."""
        parked.waiting += 1
        try:
            yield AwaitBrokerBack()
        except Exception:
            parked.waiting -= 1
            raise
        parked.waiting -= 1
        return (yield Resume(k, None))

    return program_handler(handler)


def _wakes_the_canceller_at_the_spawn(woken: Promise[None], nth: int) -> "ProgramHandler":
    """The layer under the wrapper that wakes the canceller at the ``nth`` ``Spawn`` the wrapper performs, without
    giving the spawner's turn to it yet, and lets the ``Spawn`` go on — the canceller then runs before the spawner's
    resume from the ``Spawn``."""
    seen = 0

    @do
    def handler(effect: Spawn, k: K) -> "EffectGenerator[object]":
        """Wake the canceller at the ``nth`` Spawn; every Spawn goes on."""
        nonlocal seen
        seen += 1
        if seen == nth:
            yield CompletePromise(woken, None, yield_to_woken=False)
        yield Pass(effect, k)
        return None

    return program_handler(handler)


def _tasks_left_waiting_after_a_cancel_in_the_spawn(
    party_under: Callable[[Layer], Program[object]], nth_spawn: int
) -> int:
    """Run the party (given the layer to put under its wrapper) as a task, cancel it while its ``nth_spawn``-th
    ``Spawn`` has not answered yet, and answer how many tasks still wait for the broker's return afterwards."""
    parked = _Parked()

    @do
    def cancelled_in_the_spawn() -> "EffectGenerator[None]":
        woken: Promise[None] = yield CreatePromise()
        party_made: Promise[Task[object]] = yield CreatePromise()

        @do
        def cancels() -> "EffectGenerator[None]":
            yield Wait(woken.future)
            yield Cancel((yield Wait(party_made.future)))

        def layer(program: Program[object]) -> Program[object]:
            return _counts_the_waits_for_the_return(parked)(
                _wakes_the_canceller_at_the_spawn(woken, nth_spawn)(program)
            )

        yield Spawn(cancels(), priority=PRIORITY_HIGH)
        party = yield Spawn(party_under(layer), priority=PRIORITY_NORMAL - 1)
        yield CompletePromise(party_made, party, yield_to_woken=False)
        try:
            yield Wait(party)
        except TaskCancelledError:
            # A task that outlived its spawner would be parked by now (the clock moves only when all wait).
            yield Delay(1.0)
            parked.after_the_party = parked.waiting
            return None
        raise AssertionError("the party ended by itself, not by the cancel")

    run_on_virtual_clock(cancelled_in_the_spawn())
    return parked.after_the_party


def _starting_routes() -> "tuple[NoticeRoute[object], ...]":
    """The laws' routes, with a gap told on the readers' channel when a wrapper starts."""
    return tuple(
        NoticeRoute(
            r.event_type,
            r.wire_name,
            r.channel,
            r.encode,
            r.decode,
            MarkGap(start_channels=(CHANNEL,)),
            r.reads,
        )
        for r in law_routes(PREFIX)
    )


def _refusing_senders() -> SenderOutage:
    """An outage in which the broker refuses every send and tells no return."""
    outage = SenderOutage()
    outage.detail = "the broker refuses senders"
    outage.back_told = False
    return outage


def test_a_cancel_while_the_watcher_of_the_wait_for_the_return_is_spawned_leaves_it_nowhere() -> None:
    # The broker is away at the start, so the wrapper waits for its return: ``_came_back_within`` spawns the watcher
    # (the first Spawn). The party is cancelled there.
    broker = MemoryBroker()

    @do
    def cut_then_party(layer: Layer) -> "EffectGenerator[object]":
        yield cut_broker(broker, "the in-memory broker was cut")
        source = "law-watched"
        events = patient_notice_events(source, law_routes(PREFIX), PATIENCE_SECONDS)
        party = subscribed_event_handler(EventBus(), source)(
            memory_notice_handler(broker)(layer(events(Pure(None))))
        )
        return (yield party)

    left = _tasks_left_waiting_after_a_cancel_in_the_spawn(cut_then_party, 1)
    assert left == 0, f"{left} task(s) still wait for the broker's return after the party was gone"


def test_a_cancel_while_the_gap_waiter_of_a_refused_send_is_spawned_leaves_it_nowhere() -> None:
    # A send the broker refuses marks a gap and spawns the task that tells it when the broker is back (``_marked``:
    # the second Spawn — the source task is the first). The party is cancelled there.
    broker = MemoryBroker()
    outage = _refusing_senders()

    @do
    def publishes_while_refused() -> "EffectGenerator[None]":
        yield Publish(LawNote("lost"))

    def party_under(layer: Layer) -> Program[object]:
        source = "law-marked"
        events = patient_notice_events(source, law_routes(PREFIX), PATIENCE_SECONDS)
        return subscribed_event_handler(EventBus(), source)(
            memory_notice_handler(broker)(refuses_senders(outage)(layer(events(publishes_while_refused()))))
        )

    left = _tasks_left_waiting_after_a_cancel_in_the_spawn(party_under, 2)
    assert left == 0, f"{left} task(s) still wait for the broker's return after the party was gone"


def test_a_cancel_while_the_gap_waiter_of_the_start_gaps_is_spawned_leaves_it_nowhere() -> None:
    # The start gaps the broker refuses are told when it is back: ``_started_gaps_told`` spawns the task that waits
    # for that (the second Spawn — the source task is the first). The party is cancelled there.
    broker = MemoryBroker()
    outage = _refusing_senders()

    def party_under(layer: Layer) -> Program[object]:
        source = "law-restarted"
        events = patient_notice_events(source, _starting_routes(), PATIENCE_SECONDS)
        return subscribed_event_handler(EventBus(), source)(
            memory_notice_handler(broker)(refuses_senders(outage)(layer(events(Pure(None)))))
        )

    left = _tasks_left_waiting_after_a_cancel_in_the_spawn(party_under, 2)
    assert left == 0, f"{left} task(s) still wait for the broker's return after the party was gone"
