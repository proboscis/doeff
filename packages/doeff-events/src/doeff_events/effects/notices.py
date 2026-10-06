"""Effects of a notice broker: events that reach other processes at once and are not stored (agora-redesign #3850).

The lower layer is the broker's own operations, answered by ``memory_notice_handler`` (one process — the
emulated environment and tests) or ``redis_notice_handler`` (Redis Pub/Sub):

- ``Announce`` — send a notice to whoever is subscribed to a channel right now; answers how many received it.
- ``SubscribeChannels`` / ``NextAnnouncement`` / ``CloseSubscription`` — receive notices.
- ``AwaitBrokerBack`` — the question "has the broker come back", answered by whoever can know it (the in-memory
  handler answers it itself; for Redis, ``broker_back_by_retry`` answers it with ``ProbeBroker``).
- ``ProbeBroker`` — try whether the broker can be reached right now (a connection only, no data).

Every operation that talks to the broker answers ``BrokerUnreachable`` instead of raising when the broker cannot
be reached.

A notice reaches the subscribers that are connected when it is announced, and nobody else, ever: nothing is kept
for a subscriber that subscribes later or is disconnected. A business program never uses these effects — it
keeps ``Publish`` / ``WaitForEvent``, and ``notice_events_handler`` translates.
"""

from dataclasses import dataclass
from typing import Final, final

from doeff import EffectBase


def _named(value: str, what: str) -> None:
    """Refuse an empty or non-text name where the effect is made, not where the broker fails on it."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{what} must be a non-empty string, got {value!r}")


@dataclass(frozen=True)
class BrokerUnreachable:
    """The answer of a broker operation while the broker cannot be reached: ``detail`` is the broker's (or the
    client library's) own words for why."""

    detail: str


@dataclass(frozen=True)
class Announcement:
    """One notice received on ``channel``: the wire name of its event (``name``) and the encoded text (``body``)."""

    channel: str
    name: str
    body: str


@final
class ChannelSubscription:
    """A subscription to channels, as ``SubscribeChannels`` answers it. Compared by identity; the handler that
    made it keeps what it needs to receive on it."""

    __slots__ = ("channels",)

    def __init__(self, channels: tuple[str, ...]) -> None:
        """Name the channels the subscription listens to."""
        self.channels: Final = channels

    def __repr__(self) -> str:
        """Show the channels in logs and test failures."""
        return f"ChannelSubscription({', '.join(self.channels)})"


@dataclass(frozen=True)
class Announce(EffectBase["int | BrokerUnreachable"]):
    """Send a notice to whoever is subscribed to ``channel`` right now (Redis ``PUBLISH``) and answer how many
    subscribers the broker handed it to. ``0`` is not an error: nobody is listening, and nobody will receive it."""

    channel: str
    name: str
    body: str

    def __post_init__(self) -> None:
        _named(self.channel, "Announce.channel")
        _named(self.name, "Announce.name")


@dataclass(frozen=True)
class SubscribeChannels(EffectBase["ChannelSubscription | BrokerUnreachable"]):
    """Subscribe to ``channels`` (Redis ``SUBSCRIBE``). Answers once the broker confirmed the subscription: a
    notice announced after the answer reaches it."""

    channels: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.channels, tuple) or not self.channels:
            raise ValueError(f"SubscribeChannels.channels must be a non-empty tuple of names, got {self.channels!r}")
        for channel in self.channels:
            _named(channel, "SubscribeChannels.channels")


@dataclass(frozen=True)
class NextAnnouncement(EffectBase["Announcement | BrokerUnreachable"]):
    """Wait for the next notice on ``subscription``. The wait has no time limit: it ends when a notice arrives,
    when the connection to the broker is lost (``BrokerUnreachable`` — the subscription is then dead and a new
    one must be made), or when the waiting task is cancelled."""

    subscription: ChannelSubscription


@dataclass(frozen=True)
class CloseSubscription(EffectBase[None]):
    """Give up ``subscription`` and what the handler keeps for it. Never fails: a broker that is already gone has
    nothing left to close."""

    subscription: ChannelSubscription


@dataclass(frozen=True)
class AwaitBrokerBack(EffectBase[None]):
    """Wait until the broker that answered ``BrokerUnreachable`` can be reached again.

    Answered by whoever can know it: ``memory_notice_handler`` answers when its broker is restored; for Redis,
    ``broker_back_by_retry`` (``handlers/redis_notices.py``) answers it by trying ``ProbeBroker`` at the interval
    the composition names. The answer must come when the broker is back, not at once: a source asks again after
    every failed retry.
    """


@dataclass(frozen=True)
class ProbeBroker(EffectBase["None | BrokerUnreachable"]):
    """Try whether the broker can be reached right now: only a connection, no data is read or written. Answers
    ``None`` when it can, ``BrokerUnreachable`` when it cannot. Asked only while somebody waits in
    ``AwaitBrokerBack`` (agora-redesign #3864)."""
