"""The per-subscriber bus hands an event over as the in-memory notice broker does (agora card ki-a83c715eab23):
``Publish`` returns to the publisher at once, and the subscriber it woke runs when the publisher next waits.

Before, ``Publish`` woke a waiting subscriber with ``CompletePromise``'s default, and the scheduler runs every task a
completion woke before the completer goes on (#493). A notice source — the task that reads a broker and publishes
each notice's event on the bus — then let its subscriber run a whole cycle between every two events it published,
so the subscriber's ``WaitForEvents`` took one event per cycle even when a crowd of them had come: agora's
turn-dispatcher took 905 ``LeaseReturned`` in 3 minutes one per ~0.16 s cycle, and an input event behind them
waited ~150 s.

Two cases:

- the events a publisher sends in one stretch all reach the subscriber that waited for them in one
  ``WaitForEvents``, in order;
- every event still reaches a subscriber that is busy between its waits, in order, and the subscriber runs each time
  the publisher waits (the loss #493 guarded against: the bus takes the woken waiter off before the publisher goes
  on, so what comes next waits in the subscriber's queue).

Each case runs on both scheduler implementations (docs/25-rust-scheduler.md).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

import pytest
from doeff_core_effects import SchedulerImplementation, Spawn, Wait, scheduled
from doeff_events import EventBus, Publish, WaitForEvents, subscribed_event_handler

from doeff import Program, Pure, do, run

if TYPE_CHECKING:
    from doeff import EffectGenerator

IMPLEMENTATIONS: tuple[SchedulerImplementation, ...] = ("python", "rust")


@dataclass(frozen=True)
class Came:
    """One event of the scene (its number)."""

    number: int


@dataclass(frozen=True)
class Published:
    """The publisher's ``Publish`` of event ``number`` returned."""

    number: int


@dataclass(frozen=True)
class Took:
    """The subscriber's ``WaitForEvents`` answered the events ``numbers`` (one wait)."""

    numbers: tuple[int, ...]


Entry = Published | Took


@final
class _Timeline:
    """What the publisher and the subscriber did, in the order they did it."""

    __slots__ = ("_mut_entries",)

    def __init__(self) -> None:
        """Start with nothing done."""
        self._mut_entries: tuple[Entry, ...] = ()

    def note(self, entry: Entry) -> None:
        """Add ``entry`` after everything noted so far."""
        self._mut_entries = (*self._mut_entries, entry)

    @property
    def entries(self) -> tuple[Entry, ...]:
        """Everything noted, oldest first."""
        return self._mut_entries


@do
def _noted(timeline: _Timeline, entry: Entry) -> "EffectGenerator[None]":
    """Note ``entry`` as a step of the program."""
    timeline.note(entry)
    yield Pure(None)


@do
def _nothing() -> "EffectGenerator[None]":
    """A task with nothing to do (waiting for it lets every task queued before it run)."""
    yield Pure(None)


@do
def _let_others_run() -> "EffectGenerator[None]":
    """Wait once: start a task that does nothing and wait for it, so the tasks already queued run first."""
    task = yield Spawn(_nothing())
    yield Wait(task)


@do
def _subscriber(timeline: _Timeline, wanted: int, busy: bool) -> "EffectGenerator[None]":
    """Take events with ``WaitForEvents`` until ``wanted`` came; with ``busy``, wait once after every take (a cycle
    that lets other tasks run)."""
    seen = 0
    while seen < wanted:
        came: tuple[object, ...] = yield WaitForEvents(Came)
        numbers = tuple(event.number for event in came if isinstance(event, Came))
        yield _noted(timeline, Took(numbers))
        seen += len(numbers)
        if busy:
            yield _let_others_run()


@do
def _publisher(
    timeline: _Timeline, numbers: tuple[int, ...], pause_every: int | None
) -> "EffectGenerator[None]":
    """Publish ``numbers`` in order; with ``pause_every``, wait once after every ``pause_every`` events."""
    for index, number in enumerate(numbers, start=1):
        yield Publish(Came(number))
        yield _noted(timeline, Published(number))
        if pause_every is not None and index % pause_every == 0:
            yield _let_others_run()


def _played(
    numbers: tuple[int, ...], timeline: _Timeline, *, pause_every: int | None, busy: bool
) -> Program[None]:
    """One subscriber (waiting first) and one publisher on one bus; ends when both ended."""
    bus = EventBus()

    @do
    def body() -> "EffectGenerator[None]":
        hearing = yield Spawn(
            subscribed_event_handler(bus, "subscriber", (Came,))(
                _subscriber(timeline, len(numbers), busy)
            )
        )
        # Let the subscriber reach its first wait before anything is published.
        yield _let_others_run()
        publishing = yield Spawn(
            subscribed_event_handler(bus, "publisher")(_publisher(timeline, numbers, pause_every))
        )
        yield Wait(publishing)
        yield Wait(hearing)

    return body()


def _taken(timeline: _Timeline) -> tuple[tuple[int, ...], ...]:
    """The subscriber's takes, in order (each = the events one wait answered)."""
    return tuple(entry.numbers for entry in timeline.entries if isinstance(entry, Took))


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_the_events_of_one_stretch_reach_the_waiting_subscriber_in_one_wait(
    implementation: SchedulerImplementation,
) -> None:
    numbers = tuple(range(1, 101))
    timeline = _Timeline()
    run(
        scheduled(
            _played(numbers, timeline, pause_every=None, busy=False), implementation=implementation
        )
    )
    # Every Publish returned before the subscriber ran, and its one wait took them all, in order.
    assert timeline.entries == (*(Published(number) for number in numbers), Took(numbers))


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_every_event_reaches_a_subscriber_busy_between_its_waits_in_order(
    implementation: SchedulerImplementation,
) -> None:
    numbers = tuple(range(1, 13))
    timeline = _Timeline()
    run(
        scheduled(
            _played(numbers, timeline, pause_every=3, busy=True), implementation=implementation
        )
    )
    taken = _taken(timeline)
    assert tuple(number for take in taken for number in take) == numbers
    # The publisher paused after every 3 events: the subscriber ran then, and took the 3 that had come together.
    assert taken == ((1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12))
