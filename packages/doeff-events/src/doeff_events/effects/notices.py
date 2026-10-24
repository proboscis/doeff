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

A kept channel is the exception (agora card ki-86d11cadf3be — a notice a reader never handled left a conversation's
input waiting until the reader's next start). ``Announce(kept=True)`` keeps the notice in the channel's log
(Redis Streams — ``XADD`` with a bounded length). A subscription that names a ``reader`` reads its kept channels
from that log: the handler keeps, for each open subscription, the position it read up to in each kept channel and
the notices it handed out that were not acknowledged (``AckAnnouncement``). A notice not acknowledged within the
handler's redelivery time is handed out again, and a later subscription of the same reader name (after a lost
connection, once the dead one is closed) goes on from where the closed one stopped. Two subscriptions of one name
open together each read every notice. A reader's first subscription in a handler starts at the
end of the log, like a Pub/Sub subscription: what was announced before it is caught up from the reader's records.
The position and the unacknowledged notices live in the handler (one process): a restarted process starts over.
"""

from dataclasses import dataclass
from typing import Final, final

from doeff import EffectBase


# How long a handler waits for the acknowledgement of a kept channel's notice before it hands the notice out again
# (the default of both handlers — the composition may name another). agora's target is a conversation's input taken
# into a turn within 5 s of being placed (card ki-86d11cadf3be): 2 s leaves room for the reader's own work.
KEPT_REDELIVERY_SECONDS: Final = 2.0


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
    """One notice received on ``channel``: the wire name of its event (``name``) and the encoded text (``body``).
    ``receipt`` = the notice's id in a kept channel's log, which its reader acknowledges (``AckAnnouncement``);
    ``""`` for a notice of a Pub/Sub channel, which is never acknowledged."""

    channel: str
    name: str
    body: str
    receipt: str = ""


@final
class ChannelSubscription:
    """A subscription to channels, as ``SubscribeChannels`` answers it. Compared by identity; the handler that
    made it keeps what it needs to receive on it. ``kept`` = the channels among them read from their kept log as
    ``reader`` (``()`` and ``None`` for a Pub/Sub subscription)."""

    __slots__ = ("channels", "kept", "reader")

    def __init__(self, channels: tuple[str, ...], kept: tuple[str, ...] = (), reader: str | None = None) -> None:
        """Name the channels the subscription listens to, and which of them it reads from their kept log."""
        self.channels: Final = channels
        self.kept: Final = kept
        self.reader: Final = reader

    def __repr__(self) -> str:
        """Show the channels in logs and test failures."""
        return f"ChannelSubscription({', '.join(self.channels)})"


@dataclass(frozen=True)
class Announce(EffectBase["int | BrokerUnreachable"]):
    """Send a notice to whoever is subscribed to ``channel`` right now (Redis ``PUBLISH``) and answer how many
    subscribers the broker handed it to. ``0`` is not an error: nobody is listening, and nobody will receive it.

    ``kept`` = the channel is a kept channel: the notice is added to the channel's log (Redis ``XADD``) instead, and
    the answer is ``1`` once the log holds it — every reader of the channel reads it, whenever it reads next."""

    channel: str
    name: str
    body: str
    kept: bool = False

    def __post_init__(self) -> None:
        _named(self.channel, "Announce.channel")
        _named(self.name, "Announce.name")


@dataclass(frozen=True)
class SubscribeChannels(EffectBase["ChannelSubscription | BrokerUnreachable"]):
    """Subscribe to ``channels`` (Redis ``SUBSCRIBE``). Answers once the broker confirmed the subscription: a
    notice announced after the answer reaches it.

    ``kept`` = the channels among ``channels`` that are kept channels: they are read from their log as ``reader``
    (required when ``kept`` is not empty). The handler goes on from where the reader's previous subscription
    stopped, and hands out again what the reader did not acknowledge (see the module's notes). A kept channel is
    heard over Pub/Sub too: a notice announced on it without ``kept`` (a sender that has not switched to the kept
    channel yet, while a system is redeclared) still reaches the subscription, without a receipt."""

    channels: tuple[str, ...]
    kept: tuple[str, ...] = ()
    reader: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.channels, tuple) or not self.channels:
            raise ValueError(f"SubscribeChannels.channels must be a non-empty tuple of names, got {self.channels!r}")
        for channel in self.channels:
            _named(channel, "SubscribeChannels.channels")
        if not isinstance(self.kept, tuple) or any(channel not in self.channels for channel in self.kept):
            raise ValueError(f"SubscribeChannels.kept must be a tuple of channels it subscribes, got {self.kept!r}")
        if self.kept:
            _named(self.reader, "SubscribeChannels.reader")
        elif self.reader is not None:
            raise ValueError("SubscribeChannels.reader names who reads kept channels: it needs kept channels")


@dataclass(frozen=True)
class NextAnnouncement(EffectBase["Announcement | BrokerUnreachable"]):
    """Wait for the next notice on ``subscription``. The wait has no time limit: it ends when a notice arrives,
    when the connection to the broker is lost (``BrokerUnreachable`` — the subscription is then dead and a new
    one must be made), or when the waiting task is cancelled."""

    subscription: ChannelSubscription


@dataclass(frozen=True)
class AckAnnouncement(EffectBase[None]):
    """Tell the handler that the reader of ``subscription`` has handled ``notice`` (a notice of a kept channel —
    ``notice.receipt`` is not empty): it is not handed out again. Kept by the handler only, never sent to the
    broker, so it never fails; acknowledging a notice twice, or one the handler no longer holds, changes nothing."""

    subscription: ChannelSubscription
    notice: Announcement

    def __post_init__(self) -> None:
        _named(self.notice.receipt, "AckAnnouncement.notice.receipt")


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
