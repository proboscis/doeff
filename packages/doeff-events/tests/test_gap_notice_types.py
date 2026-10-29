"""The gap notice is made and told apart through the types, not through its wire name (ADR-DOE-API-001 R1·R2 —
agora card ki-a2ac882abeb7): ``Announce.gap(channel, source)`` sends it, and ``Announce.is_gap`` (for a fake broker
that sees the effect) and ``Announcement.is_gap`` (for a reader of raw notices) tell it apart from a routed notice."""

from typing import TYPE_CHECKING

from doeff_core_effects import scheduled
from doeff_events import (
    Announce,
    Announcement,
    BrokerUnreachable,
    ChannelSubscription,
    MemoryBroker,
    NextAnnouncement,
    SubscribeChannels,
    memory_notice_handler,
)

from doeff import do, run

if TYPE_CHECKING:
    from doeff import EffectGenerator

CHANNEL = "gap-test"


def _kind(notice: Announcement) -> str:
    """Name a received notice the way a caller's ``match`` does (the keyword pattern reads the property)."""
    match notice:
        case Announcement(is_gap=True):
            return f"gap from {notice.body}"
        case Announcement(name=name):
            return f"{name}: {notice.body}"


def test_announce_gap_is_a_gap_and_a_routed_notice_is_not() -> None:
    gap = Announce.gap(CHANNEL, "sender")
    assert gap.is_gap
    assert (gap.channel, gap.body, gap.kept) == (CHANNEL, "sender", False)
    assert Announce.gap(CHANNEL, "sender", kept=True).kept
    assert not Announce(CHANNEL, "note", "{}").is_gap


def test_the_received_notice_of_a_gap_is_a_gap_with_the_source_as_its_body() -> None:
    broker = MemoryBroker()

    @do
    def body() -> "EffectGenerator[tuple[str, str]]":
        subscription: ChannelSubscription | BrokerUnreachable = yield SubscribeChannels((CHANNEL,))
        assert isinstance(subscription, ChannelSubscription), subscription
        yield Announce.gap(CHANNEL, "sender")
        yield Announce(CHANNEL, "note", "{}")
        first: Announcement | BrokerUnreachable = yield NextAnnouncement(subscription)
        second: Announcement | BrokerUnreachable = yield NextAnnouncement(subscription)
        assert isinstance(first, Announcement), first
        assert isinstance(second, Announcement), second
        return _kind(first), _kind(second)

    assert run(scheduled(memory_notice_handler(broker)(body()))) == ("gap from sender", "note: {}")
