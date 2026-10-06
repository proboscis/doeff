"""Effects of an event broker that carries events between processes (agora-redesign #3850).

Two layers.

The lower layer is the broker's own operations, answered by ``memory_stream_handler`` (one process — the
emulated environment and tests) or ``redis_stream_handler`` (Redis Streams with consumer groups, and Pub/Sub):

- an acknowledged stream: ``AppendEntry`` / ``EnsureGroup`` / ``ReadGroup`` / ``AckEntry`` / ``ClaimPending`` /
  ``ReadGroupPosition`` / ``ReadEntryRange``. One entry goes to one consumer of a group, stays in the group's
  pending list until it is acknowledged, and can be claimed by another consumer of the group.
- a notice that only reaches whoever is connected: ``Announce`` / ``SubscribeChannels`` / ``NextAnnouncement``.
- ``AwaitBrokerBack``: the question "has the broker come back", answered by whoever can know it (the in-memory
  handler answers it itself; the Redis handler passes it outward to the composition).

Every operation answers ``BrokerUnreachable`` instead of raising when the broker cannot be reached.

The upper layer is what a business program may use beside ``Publish`` / ``WaitForEvent``: ``EventsBetween``,
answered by ``stream_events_handler``. Stream names, group names, consumer names and acknowledgements never
appear in a business program — only the lower layer and the handler's routes carry them.

A position is the broker's id of an entry in its stream (Redis: ``<milliseconds>-<sequence>``). It travels as a
plain string; ``position_key`` gives the order.
"""

from dataclasses import dataclass
from typing import Final, Generic, TypeVar, final

from doeff import EffectBase

_E = TypeVar("_E")

ORIGIN: Final = "0-0"
"""The position before every entry of every stream."""


@dataclass(frozen=True, order=True)
class PositionKey:
    """A position as numbers, so that positions can be compared: earlier in the stream is smaller."""

    major: int
    minor: int


def position_key(position: str) -> PositionKey:
    """The order of positions: ``position_key(a) < position_key(b)`` when ``a`` is earlier in the stream."""
    head, separator, tail = position.partition("-")
    if not separator or not head.isdigit() or not tail.isdigit():
        raise ValueError(f"a stream position is '<number>-<number>', got {position!r}")
    return PositionKey(int(head), int(tail))


def _named(value: str, what: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{what} must be a non-empty string, got {value!r}")


def _all_named(values: tuple[str, ...], what: str) -> None:
    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{what} must be a non-empty tuple of names, got {values!r}")
    for value in values:
        _named(value, what)


@dataclass(frozen=True)
class BrokerUnreachable:
    """The answer of any broker operation while the broker cannot be reached: ``detail`` is the broker's (or the
    client library's) own words for why."""

    detail: str


@dataclass(frozen=True)
class StreamEntry:
    """One entry of a stream: where it is (``stream``, ``position``), the wire name of its event (``name``) and
    the event's encoded text (``body``)."""

    stream: str
    position: str
    name: str
    body: str


@dataclass(frozen=True)
class ClaimedEntries:
    """The answer of ``ClaimPending``: ``entries`` are now pending for the claiming consumer; ``lost`` are the
    positions that were pending but whose entries are gone from the stream (cut by a length limit)."""

    entries: tuple[StreamEntry, ...]
    lost: tuple[str, ...]


@dataclass(frozen=True)
class GroupPosition:
    """Where a group stands in its stream (the answer of ``ReadGroupPosition``).

    ``last_delivered`` = the position of the last entry given to the group (``ORIGIN`` when none),
    ``first`` = the position of the oldest entry still in the stream (``None`` when the stream is empty),
    ``last_generated`` = the position of the newest entry the stream ever had (``ORIGIN`` when none).
    """

    last_delivered: str
    first: str | None
    last_generated: str


def head_was_cut(position: GroupPosition) -> bool:
    """Whether entries the group had not been given may have been cut from the head of its stream.

    The one rule both brokers share. A length limit only removes the oldest entries, so if any entry after
    ``last_delivered`` was cut, the oldest remaining entry is later than ``last_delivered`` (or nothing remains
    and the stream's newest id is later). The rule never misses a cut. It also answers ``True`` in one case
    where nothing was lost: the cut removed exactly the entries up to and including ``last_delivered`` — the
    broker keeps no record that tells the two apart, and a reader that catches up once too often is safe.
    """
    delivered = position_key(position.last_delivered)
    if position.first is None:
        return delivered < position_key(position.last_generated)
    return delivered < position_key(position.first)


@dataclass(frozen=True)
class EntryRange:
    """The answer of ``ReadEntryRange``: the entries after ``after`` up to and including ``until``, in order.

    ``complete`` is ``False`` when the head of the range may have been cut: the entry at ``after`` is no longer
    in the stream and the stream has lost entries (the same conservative rule as ``head_was_cut``).
    """

    entries: tuple[StreamEntry, ...]
    complete: bool


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
class AppendEntry(EffectBase["str | BrokerUnreachable"]):
    """Append an entry to ``stream`` and answer its position (Redis ``XADD``).

    ``maxlen`` cuts the oldest entries so that about ``maxlen`` remain (Redis ``MAXLEN ~``: the broker may keep
    somewhat more, never fewer); ``None`` never cuts.
    """

    stream: str
    name: str
    body: str
    maxlen: int | None = None

    def __post_init__(self) -> None:
        _named(self.stream, "AppendEntry.stream")
        _named(self.name, "AppendEntry.name")
        if self.maxlen is not None and (isinstance(self.maxlen, bool) or self.maxlen < 1):
            raise ValueError(f"AppendEntry.maxlen must be a positive integer or None, got {self.maxlen!r}")


@dataclass(frozen=True)
class EnsureGroup(EffectBase["None | BrokerUnreachable"]):
    """Make the consumer group ``group`` on ``stream`` (and the stream itself) unless it already exists.

    A new group starts after the stream's newest entry: it is given only entries appended from then on
    (Redis ``XGROUP CREATE <stream> <group> $ MKSTREAM``). An existing group is left as it is.
    """

    stream: str
    group: str

    def __post_init__(self) -> None:
        _named(self.stream, "EnsureGroup.stream")
        _named(self.group, "EnsureGroup.group")


@dataclass(frozen=True)
class ReadGroup(EffectBase["tuple[StreamEntry, ...] | BrokerUnreachable"]):
    """Wait until ``streams`` have entries not yet given to ``group`` and take them as ``consumer``
    (Redis ``XREADGROUP ... BLOCK 0 ... >``). Answers one entry or more; each stays pending until ``AckEntry``.

    The wait has no time limit. It ends when an entry arrives, when the broker becomes unreachable, or when the
    waiting task is cancelled.
    """

    streams: tuple[str, ...]
    group: str
    consumer: str

    def __post_init__(self) -> None:
        _all_named(self.streams, "ReadGroup.streams")
        _named(self.group, "ReadGroup.group")
        _named(self.consumer, "ReadGroup.consumer")


@dataclass(frozen=True)
class AckEntry(EffectBase["None | BrokerUnreachable"]):
    """Take the entry at ``position`` out of ``group``'s pending list (Redis ``XACK``). Acknowledging an entry
    that is not pending does nothing."""

    stream: str
    group: str
    position: str

    def __post_init__(self) -> None:
        _named(self.stream, "AckEntry.stream")
        _named(self.group, "AckEntry.group")
        position_key(self.position)


@dataclass(frozen=True)
class ClaimPending(EffectBase["ClaimedEntries | BrokerUnreachable"]):
    """Hand every pending entry of ``group`` on ``stream`` to ``consumer``, whoever held it and however briefly
    (Redis ``XAUTOCLAIM`` with a minimum idle time of 0, read to its end)."""

    stream: str
    group: str
    consumer: str

    def __post_init__(self) -> None:
        _named(self.stream, "ClaimPending.stream")
        _named(self.group, "ClaimPending.group")
        _named(self.consumer, "ClaimPending.consumer")


@dataclass(frozen=True)
class ReadGroupPosition(EffectBase["GroupPosition | BrokerUnreachable"]):
    """Read where ``group`` stands in ``stream`` (Redis ``XINFO GROUPS`` and ``XINFO STREAM``)."""

    stream: str
    group: str

    def __post_init__(self) -> None:
        _named(self.stream, "ReadGroupPosition.stream")
        _named(self.group, "ReadGroupPosition.group")


@dataclass(frozen=True)
class ReadEntryRange(EffectBase["EntryRange | BrokerUnreachable"]):
    """Read the entries of ``stream`` after ``after`` up to and including ``until`` once (Redis ``XRANGE``).

    A plain read: no group is involved and no pending list changes.
    """

    stream: str
    after: str
    until: str

    def __post_init__(self) -> None:
        _named(self.stream, "ReadEntryRange.stream")
        position_key(self.after)
        position_key(self.until)


@dataclass(frozen=True)
class Announce(EffectBase["None | BrokerUnreachable"]):
    """Send a notice to whoever is subscribed to ``channel`` right now (Redis ``PUBLISH``). Nobody else ever
    receives it."""

    channel: str
    name: str
    body: str

    def __post_init__(self) -> None:
        _named(self.channel, "Announce.channel")
        _named(self.name, "Announce.name")


@dataclass(frozen=True)
class SubscribeChannels(EffectBase["ChannelSubscription | BrokerUnreachable"]):
    """Subscribe to ``channels`` (Redis ``SUBSCRIBE``). Answers once the subscription is in place: a notice
    announced after the answer reaches it."""

    channels: tuple[str, ...]

    def __post_init__(self) -> None:
        _all_named(self.channels, "SubscribeChannels.channels")


@dataclass(frozen=True)
class NextAnnouncement(EffectBase["Announcement | BrokerUnreachable"]):
    """Wait for the next notice on ``subscription``. No time limit, like ``ReadGroup``."""

    subscription: ChannelSubscription


@dataclass(frozen=True)
class AwaitBrokerBack(EffectBase[None]):
    """Wait until the broker that answered ``BrokerUnreachable`` can be reached again.

    Answered by whoever can know it: ``memory_stream_handler`` answers when its broker is restored; the Redis
    handler does not answer it — the composition does (for example from the readiness of the broker's service).
    The answer must come when the broker is back, not at once: a source asks again after every failed retry.
    """


@dataclass(frozen=True)
class EventsRead:
    """The answer of ``EventsBetween`` when the whole range was read: the routed events in it, in stream order."""

    events: tuple[object, ...]


@dataclass(frozen=True)
class RangeCut:
    """The answer of ``EventsBetween`` when the head of the range was cut from the stream (a length limit), so the
    range cannot be read whole: ``events`` are the routed events that remain, in stream order. The program
    catches up from its records instead."""

    events: tuple[object, ...]


@dataclass(frozen=True)
class EventsBetween(EffectBase["EventsRead | RangeCut"], Generic[_E]):
    """Read once the events of ``event_type``'s stream after position ``after`` up to and including ``until``.

    The stream is the one the handler's routes read for ``event_type``; the answer holds every routed event in
    the range (of any routed type of that stream), each decoded with its position. It is a plain read beside the
    subscription: nothing is acknowledged and nothing the subscription delivers changes.
    """

    event_type: type[_E]
    after: str
    until: str

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, type):
            raise TypeError(f"EventsBetween.event_type must be a type, got {self.event_type!r}")
        position_key(self.after)
        position_key(self.until)
