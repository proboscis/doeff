"""The in-memory broker hands a notice over as Redis Pub/Sub does: ``Announce`` returns to the writer at once and
the subscribers it woke run when the writer next waits — the writer does not wait for them (agora-redesign #4013).

Before, ``Announce`` woke a waiting subscriber with ``CompletePromise``, and the scheduler runs every task a
completion woke before the completer goes on (#493). A writer that sent several notices in one stretch then let the
subscriber run between every two of them, so the subscriber took the notices one by one and ran its whole reaction
once per notice — a run the production broker never makes.

Three cases:

- the writer's notices of one stretch are all sent before the subscriber that waited for them runs, and it then
  takes them in order;
- every notice still reaches a subscriber that is busy between its receipts, in order (the loss #493 guarded
  against: a writer sending while the woken subscriber has not asked for the next notice yet — the broker keeps
  such notices in the subscriber's queue);
- a subscriber nobody sends to is still a dead end the scheduler names, before and after notices came.

Each case runs on both scheduler implementations (docs/25-rust-scheduler.md).
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, final

import pytest
from doeff_core_effects.scheduler import (
    PRIORITY_HIGH,
    PRIORITY_NORMAL,
    SchedulerDeadlockError,
    SchedulerImplementation,
    Spawn,
    Wait,
    scheduled,
)
from doeff_events.effects.notices import (
    Announce,
    Announcement,
    BrokerUnreachable,
    ChannelSubscription,
    NextAnnouncement,
    SubscribeChannels,
)
from doeff_events.handlers.memory_notices import MemoryBroker, memory_notice_handler

from doeff import Program, Pure, do, run

if TYPE_CHECKING:
    from doeff import EffectGenerator

IMPLEMENTATIONS: tuple[SchedulerImplementation, ...] = ("python", "rust")
CHANNEL = "turn-test"


@dataclass(frozen=True)
class Sent:
    """The writer's ``Announce`` of ``body`` returned."""

    body: str


@dataclass(frozen=True)
class Heard:
    """The subscriber received the notice ``body``."""

    body: str


@dataclass(frozen=True)
class Handled:
    """The subscriber finished its reaction to the notice ``body`` (a reaction that waits)."""

    body: str


Entry = Sent | Heard | Handled


@final
class _Timeline:
    """What the writer and the subscriber did, in the order they did it."""

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
def _subscriber(
    timeline: _Timeline, subscription: ChannelSubscription, wanted: int, reaction_waits: bool
) -> "EffectGenerator[None]":
    """Take ``wanted`` notices one after another; with ``reaction_waits``, wait once while reacting to each."""
    for _ in range(wanted):
        notice: Announcement | BrokerUnreachable = yield NextAnnouncement(subscription)
        assert isinstance(notice, Announcement), notice
        yield _noted(timeline, Heard(notice.body))
        if reaction_waits:
            yield _let_others_run()
            yield _noted(timeline, Handled(notice.body))


@do
def _writer(
    timeline: _Timeline,
    broker: MemoryBroker,
    subscription: ChannelSubscription,
    bodies: tuple[str, ...],
    pause_every: int | None,
) -> "EffectGenerator[None]":
    """Send ``bodies`` in order; with ``pause_every``, wait once after every ``pause_every`` notices."""
    listener = broker.listener(subscription)
    assert listener is not None
    assert listener.waiter is not None, "the subscriber must be waiting before the writer sends"
    for index, body in enumerate(bodies, start=1):
        yield Announce(CHANNEL, "note", body)
        yield _noted(timeline, Sent(body))
        if pause_every is not None and index % pause_every == 0:
            yield _let_others_run()


@dataclass(frozen=True)
class _Scene:
    """One writer and one subscriber of ``CHANNEL`` on one broker."""

    bodies: tuple[str, ...]
    wanted: int
    writer_priority: int = PRIORITY_NORMAL
    pause_every: int | None = None
    reaction_waits: bool = False


def _played(scene: _Scene, timeline: _Timeline) -> Program[None]:
    """The scene as one program: subscribe, start the subscriber (it waits for its first notice), then the writer,
    and end when both ended."""
    broker = MemoryBroker()

    @do
    def body() -> "EffectGenerator[None]":
        subscription: ChannelSubscription | BrokerUnreachable = yield SubscribeChannels((CHANNEL,))
        assert isinstance(subscription, ChannelSubscription), subscription
        hearing = yield Spawn(
            _subscriber(timeline, subscription, scene.wanted, scene.reaction_waits)
        )
        writing = yield Spawn(
            _writer(timeline, broker, subscription, scene.bodies, scene.pause_every),
            priority=scene.writer_priority,
        )
        yield Wait(writing)
        yield Wait(hearing)

    return memory_notice_handler(broker)(body())


def _heard(timeline: _Timeline) -> tuple[str, ...]:
    """The bodies the subscriber received, in order."""
    return tuple(entry.body for entry in timeline.entries if isinstance(entry, Heard))


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_the_writer_sends_its_three_notices_before_the_waiting_subscriber_runs(
    implementation: SchedulerImplementation,
) -> None:
    timeline = _Timeline()
    run(
        scheduled(
            _played(_Scene(bodies=("1", "2", "3"), wanted=3), timeline),
            implementation=implementation,
        )
    )
    assert timeline.entries == (Sent("1"), Sent("2"), Sent("3"), Heard("1"), Heard("2"), Heard("3"))


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
@pytest.mark.parametrize(
    "writer_priority", [PRIORITY_NORMAL, PRIORITY_HIGH], ids=["normal-writer", "high-writer"]
)
def test_every_notice_reaches_a_subscriber_busy_between_its_receipts_in_order(
    implementation: SchedulerImplementation, writer_priority: int
) -> None:
    bodies = tuple(str(number) for number in range(1, 7))
    scene = _Scene(
        bodies=bodies,
        wanted=len(bodies),
        writer_priority=writer_priority,
        pause_every=2,
        reaction_waits=True,
    )
    timeline = _Timeline()
    run(scheduled(_played(scene, timeline), implementation=implementation))
    assert _heard(timeline) == bodies
    # The case has teeth only if the writer sent while the subscriber was busy with an earlier notice (it had not
    # asked for the next one yet): such a notice can only reach it through the broker's queue.
    entries = timeline.entries
    sent_while_busy = tuple(
        entry
        for position, entry in enumerate(entries)
        if isinstance(entry, Sent)
        and any(
            isinstance(before, Heard) and Handled(before.body) in entries[position:]
            for before in entries[:position]
        )
    )
    assert sent_while_busy, entries


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_a_subscriber_nobody_sends_to_is_a_dead_end_the_scheduler_names(
    implementation: SchedulerImplementation,
) -> None:
    # The answer measured before #4013 changed how Announce wakes the subscriber: unchanged.
    with pytest.raises(SchedulerDeadlockError) as raised:
        run(
            scheduled(
                _played(_Scene(bodies=(), wanted=1), _Timeline()), implementation=implementation
            )
        )
    assert str(raised.value) == (
        "scheduler deadlock: parked waiters that no external completion can wake: "
        "task 0 (wait) on promise 1; root (wait) on task 0"
    )
    assert raised.value.semaphore_waiters == {}
    assert raised.value.parked_waiters == ["task 0 (wait) on promise 1", "root (wait) on task 0"]


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_a_subscriber_left_waiting_after_the_notices_came_is_a_dead_end_the_scheduler_names(
    implementation: SchedulerImplementation,
) -> None:
    # Two notices come and the subscriber waits for a third nobody sends. The answer names the same two parked
    # waiters as above; the number of the promise the subscriber waits on depends on how many promises the run made
    # before it, and the list follows the order in which the two parked.
    timeline = _Timeline()
    with pytest.raises(SchedulerDeadlockError) as raised:
        run(
            scheduled(
                _played(_Scene(bodies=("1", "2"), wanted=3), timeline),
                implementation=implementation,
            )
        )
    assert _heard(timeline) == ("1", "2")
    assert raised.value.semaphore_waiters == {}
    parked = raised.value.parked_waiters
    subscriber_parked = tuple(
        waiter for waiter in parked if re.fullmatch(r"task 0 \(wait\) on promise \d+", waiter)
    )
    assert len(subscriber_parked) == 1, parked
    assert sorted(parked) == sorted([*subscriber_parked, "root (wait) on task 0"])
    assert str(raised.value) == (
        "scheduler deadlock: parked waiters that no external completion can wake: "
        + "; ".join(parked)
    )
