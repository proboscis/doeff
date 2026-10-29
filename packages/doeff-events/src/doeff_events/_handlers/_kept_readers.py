"""Which state of a kept channel's reader each open subscription holds — the one place both broker handlers keep it
(agora card ki-86d11cadf3be).

A reader's state (its positions in the kept logs and the notices it holds without an acknowledgement) belongs to one
open subscription at a time. Two subscriptions of the same reader name that are open together each get their own
state, so neither takes notices from the other (a process that remakes its readers, like a screen server, keeps
every notice reaching every open subscription — the meaning a Pub/Sub subscription has). When a subscription is
closed its state is kept, and the next subscription of the same name goes on from it (the source that lost its
connection closes the dead subscription before it subscribes again).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar, final

from doeff_events._effects.notices import ChannelSubscription

_R = TypeVar("_R")


@dataclass(frozen=True)
class _Held(Generic[_R]):
    """One open subscription and the reader state it holds."""

    subscription: ChannelSubscription
    state: _R


@final
class KeptReaders(Generic[_R]):
    """The reader states of one handler: those held by open subscriptions, and those left by closed ones (per name,
    newest last)."""

    __slots__ = ("_fresh", "_mut_held", "_mut_left")

    def __init__(self, fresh: Callable[[], _R]) -> None:
        """Start with no reader; ``fresh`` makes the state of a reader that has read nothing."""
        self._fresh = fresh
        self._mut_held: tuple[_Held[_R], ...] = ()
        self._mut_left: dict[str, tuple[_R, ...]] = {}

    def attach(self, name: str) -> _R:
        """The state a new subscription of ``name`` reads with: the newest one a closed subscription of the same name
        left, or a fresh one. ``held`` must follow once the subscription exists."""
        left = self._mut_left.get(name, ())
        if not left:
            return self._fresh()
        self._mut_left = {**self._mut_left, name: left[:-1]}
        return left[-1]

    def returned(self, name: str, state: _R) -> None:
        """Give back a state ``attach`` handed out for a subscription that could not be made."""
        self._mut_left = {**self._mut_left, name: (*self._mut_left.get(name, ()), state)}

    def held(self, subscription: ChannelSubscription, state: _R) -> None:
        """Record that the open ``subscription`` holds ``state``."""
        self._mut_held = (*self._mut_held, _Held(subscription, state))

    def of(self, subscription: ChannelSubscription) -> _R | None:
        """The state ``subscription`` holds (``None`` once it is closed, or for a Pub/Sub subscription)."""
        return next((held.state for held in self._mut_held if held.subscription is subscription), None)

    def detach(self, subscription: ChannelSubscription) -> None:
        """``subscription`` is closed: keep its state for the next subscription of the same reader name."""
        closing = tuple(held for held in self._mut_held if held.subscription is subscription)
        self._mut_held = tuple(held for held in self._mut_held if held.subscription is not subscription)
        name = subscription.reader
        if name is None:
            return
        for held in closing:
            self.returned(name, held.state)
