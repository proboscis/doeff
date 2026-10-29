"""Deadlines as events: arm a timer, and receive ``TimerFired`` through ``WaitForEvent`` (agora-redesign #3076).

A program does not wait for time. It arms a timer with ``ArmTimer(tag, at)`` and goes on waiting for events;
at ``at`` the timer handler publishes ``TimerFired(tag)``, which the program receives with
``WaitForEvent(TimerFired, ...)`` like any other event. When the thing the deadline guarded happens first,
the program either disarms the timer (``DisarmTimer(tag)`` — the timer never fires) or ignores a
``TimerFired`` whose tag is not the one its state expects (the tag is how a stale deadline is told apart).
"""

from collections.abc import Hashable
from dataclasses import dataclass
from datetime import datetime

from doeff import EffectBase


@dataclass(frozen=True)
class TimerFired:
    """The event a timer publishes at its deadline: ``tag`` is the tag it was armed with."""

    tag: Hashable


@dataclass(frozen=True)
class ArmTimerEffect(EffectBase):
    """Arm the timer ``tag`` to fire at ``at`` (a timezone-aware instant).

    Arming a tag that is already armed moves its deadline: the earlier arming is disarmed, so one tag fires
    at most once per arming. A deadline at or before now fires as soon as the clock handler next lets
    time pass.
    """

    tag: Hashable
    at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.at, datetime):
            raise TypeError(f"at must be datetime, got {type(self.at).__name__}")
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("at must be timezone-aware")


@dataclass(frozen=True)
class DisarmTimerEffect(EffectBase):
    """Disarm the timer ``tag``: it does not fire. Disarming a tag that is not armed does nothing."""

    tag: Hashable


@dataclass(frozen=True)
class ArmedTimer:
    """One armed timer as ``ArmedTimers`` answers it: its tag and its deadline."""

    tag: Hashable
    at: datetime


@dataclass(frozen=True)
class ArmedTimersEffect(EffectBase):
    """Ask which timers are armed now: answers ``tuple[ArmedTimer, ...]``, earliest deadline first.

    Only the timers armed with ``ArmTimer`` are listed — the programs' deadlines, not the clock's own waits
    (a sim host's quiet-beat wait goes to the clock directly), so an empty answer tells a sim watcher that
    no deadline will ever wake a program (agora-redesign #3078).
    """


# The effects' constructors under the names the programs use (the classes themselves — a typed answer, no wrapper).
ArmTimer = ArmTimerEffect
DisarmTimer = DisarmTimerEffect
ArmedTimers = ArmedTimersEffect
