"""Check of ``CountQueued`` — how many signals of the named types wait in the subscriber's own queue.

The queue has no bound, so a receiver that takes one signal per ``WaitForEvent`` reads its backlog with this.

- Signals published before the count are counted, per named type, and stay in the queue (the waits after it still
  receive them in arrival order).
- A merged signal (one with ``keys``) counts once.
- Counting a type outside the subscription raises ``ValueError`` (the same check as the waits).
"""

from dataclasses import dataclass

import pytest
from doeff_core_effects import Spawn, Wait
from doeff_events import CountQueued, EventBus, Publish, WaitForEvent, subscribed_event_handler
from events_test_support import run_scheduled

from doeff import EffectGenerator, Program, do


@dataclass(frozen=True)
class Rang:
    """A signal the receiver subscribes to (no ``keys``: never merged)."""

    room: str


@dataclass(frozen=True)
class Knocked:
    """A second subscribed type."""

    door: str


@dataclass(frozen=True)
class Changed:
    """A mergeable signal: same type and fields apart from ``keys`` become one in the queue."""

    keys: tuple[str, ...]


@dataclass(frozen=True)
class Unrelated:
    """A signal outside the receiver's subscription types."""

    note: str


@do
def _publish_all(events: tuple[object, ...]) -> EffectGenerator[None]:
    """Writer: emit ``events`` in order."""
    for event in events:
        yield Publish(event)


def _answer_after(published: tuple[object, ...], body: Program[object]) -> object:
    """The answer of ``body`` run by the subscriber ``listener`` (subscribed to ``Rang``, ``Knocked`` and ``Changed``)
    after the writer has finished emitting ``published``."""
    bus = EventBus()
    receiving = subscribed_event_handler(bus, "listener", (Rang, Knocked, Changed))
    sending = subscribed_event_handler(bus, "writer")

    @do
    def main() -> EffectGenerator[object]:
        """Start the receiver's subscription, finish running the writer, and then run the receiver."""
        yield Wait((yield Spawn(sending(_publish_all(published)))))
        answer: object = yield receiving(body)
        return answer

    return run_scheduled(main())


def test_count_queued_counts_per_type_and_takes_nothing() -> None:
    @do
    def counts_then_takes() -> EffectGenerator[tuple[object, ...]]:
        rang = yield CountQueued(Rang)
        both = yield CountQueued(Rang, Knocked)
        first = yield WaitForEvent(Rang, Knocked)
        after = yield CountQueued(Rang, Knocked)
        return (rang, both, first, after)

    answer = _answer_after((Rang("a"), Knocked("front"), Rang("b")), counts_then_takes())
    assert answer == (2, 3, Rang("a"), 2), answer


def test_count_queued_counts_a_merged_signal_once() -> None:
    @do
    def count() -> EffectGenerator[int]:
        counted: int = yield CountQueued(Changed)
        return counted

    assert _answer_after((Changed(("x",)), Changed(("y",))), count()) == 1


def test_count_queued_of_a_type_outside_the_subscription_is_refused() -> None:
    @do
    def count() -> EffectGenerator[int]:
        counted: int = yield CountQueued(Unrelated)
        return counted

    with pytest.raises(ValueError, match="outside its subscription: Unrelated"):
        _answer_after((), count())
