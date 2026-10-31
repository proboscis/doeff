"""Provider-agnostic time effects."""


import math
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from doeff import EffectBase
from doeff_time._internals.validation import ensure_aware_datetime

if TYPE_CHECKING:
    from doeff_core_effects import Future, Task

_T = TypeVar("_T")


def _coerce_finite_float(value: float, *, name: str) -> float:
    if not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be float, got {type(value).__name__}")
    coerced = float(value)
    if math.isnan(coerced) or math.isinf(coerced):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return coerced


@dataclass(frozen=True)
class DelayEffect(EffectBase):
    """Sleep for a duration in seconds."""

    seconds: float

    def __post_init__(self) -> None:
        seconds = _coerce_finite_float(self.seconds, name="seconds")
        if seconds < 0.0:
            raise ValueError("seconds must be >= 0.0")
        object.__setattr__(self, "seconds", seconds)


@dataclass(frozen=True)
class WaitUntilEffect(EffectBase):
    """Wait until a specific timezone-aware datetime."""

    target: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", ensure_aware_datetime(self.target, name="target"))


@dataclass(frozen=True)
class GetTimeEffect(EffectBase):
    """Read current timezone-aware datetime."""


@dataclass(frozen=True)
class GetMonotonicEffect(EffectBase):
    """Read monotonic seconds as a float.

    Only the difference between two readings is meaningful (durations,
    deadlines inside one process). Unlike ``GetTime`` it never jumps backwards
    with wall-clock adjustments. The origin is handler-defined: wall-clock
    handlers answer ``time.monotonic()``; ``sim_time_handler`` answers the
    virtual clock's POSIX timestamp.
    """


@dataclass(frozen=True)
class ScheduleAtEffect(EffectBase):
    """Schedule a program for execution at a specific timezone-aware datetime."""

    time: datetime
    program: Any

    def __post_init__(self) -> None:
        object.__setattr__(self, "time", ensure_aware_datetime(self.time, name="time"))


@dataclass(frozen=True)
class WaitWithinEffect(EffectBase, Generic[_T]):
    """Wait for a scheduler future for at most ``seconds``.

    Answers the future's value, or ``None`` when ``seconds`` pass first — so the
    future's producer must not complete it with ``None``. One effect replaces the
    "spawn a sleeping timer task, Race it, cancel it" pattern: the clock handler
    owns the deadline (``sim_time_handler`` puts it on its virtual time queue and
    drops it when the future wins — no task is spawned), which keeps a timed wait
    as cheap as a Delay.

    ``park`` is for an *external* promise that in-run code completes (doeff-records'
    memory-store bell, rung by a synchronous write): the race against the deadline
    parks like ``Wait(future, priority=TaskPriority.IDLE)`` instead of shielding the sim
    clock, so virtual time can reach the deadline while the promise is pending. With
    the default (``False``) a pending external promise holds the clock, and the
    deadline cannot pass before it completes.
    """

    future: "Future[_T]"
    seconds: float
    park: bool = False

    def __post_init__(self) -> None:
        seconds = _coerce_finite_float(self.seconds, name="seconds")
        if seconds < 0.0:
            raise ValueError("seconds must be >= 0.0")
        if not isinstance(self.park, bool):
            raise TypeError(f"park must be bool, got {type(self.park).__name__}")
        object.__setattr__(self, "seconds", seconds)


@dataclass(frozen=True)
class RaceOutcome(Generic[_T]):
    """The answer of RaceWithin: ``value`` = the value of the first of the futures to complete (None when the
    deadline passed first) and ``timed_out`` = whether the deadline passed first."""

    value: "_T | None"
    timed_out: bool


@dataclass(frozen=True)
class RaceWithinEffect(EffectBase[RaceOutcome[_T]], Generic[_T]):
    """Wait for the first of several scheduler futures or tasks for at most ``seconds``.

    WaitWithin for a Race: answers ``RaceOutcome(value, False)`` with the value of the first of ``futures``
    to complete (a failure or a cancellation is raised, as ``Race`` does), or ``RaceOutcome(None, True)`` when
    ``seconds`` pass first. A future may complete with ``None``: the deadline is told apart by ``timed_out``,
    not by the value. One effect replaces "spawn a sleeping timer task, Race it against the futures, cancel
    it": ``sim_time_handler`` puts the deadline on its virtual time queue and withdraws it when a future wins
    — no task is spawned (doeff-cluster's worker tick pause races its wake bells against the next due time
    this way). ``park`` is WaitWithin's.
    """

    futures: "tuple[Task[_T] | Future[_T], ...]"
    seconds: float
    park: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.futures, tuple):
            raise TypeError(f"futures must be a tuple, got {type(self.futures).__name__}")
        if not self.futures:
            raise ValueError("futures must hold at least one Task or Future (a bare deadline is a Delay)")
        seconds = _coerce_finite_float(self.seconds, name="seconds")
        if seconds < 0.0:
            raise ValueError("seconds must be >= 0.0")
        if not isinstance(self.park, bool):
            raise TypeError(f"park must be bool, got {type(self.park).__name__}")
        object.__setattr__(self, "seconds", seconds)


@dataclass(frozen=True)
class TicksOutcome(Generic[_T]):
    """The answer of WaitTicks: ``value`` = the future's value (None when the ticks ran out first) and
    ``passed`` = how many ticks passed before the answer (0 to ``count``)."""

    value: "_T | None"
    passed: int


@dataclass(frozen=True)
class WaitTicksEffect(EffectBase[TicksOutcome[_T]], Generic[_T]):
    """Wait for a scheduler future across a run of ``count`` ticks spaced ``every`` seconds, without waking
    at each tick.

    The ticks are at ``every``, ``2 * every`` … ``count * every`` seconds from now. Answers
    ``TicksOutcome(value, passed)``: the future's value when it completes first (``passed`` = the ticks
    that passed before it), or ``TicksOutcome(None, count)`` when the last tick passes first.

    The order at a tick instant is that of a waiter that re-registers ``WaitWithin(future, every)`` at
    each tick: tick k counts as passed before any other timer due at the same
    instant that was registered after tick k-1 passed, and after one registered before it.
    ``sim_time_handler`` keeps that order by re-queueing tick k+1 on its virtual time queue the moment
    tick k is taken off it — the waiting task is not woken at the ticks. ``park`` is WaitWithin's.
    """

    future: "Future[_T]"
    every: float
    count: int
    park: bool = False

    def __post_init__(self) -> None:
        every = _coerce_finite_float(self.every, name="every")
        if every <= 0.0:
            raise ValueError("every must be > 0.0")
        if isinstance(self.count, bool) or not isinstance(self.count, int):
            raise TypeError(f"count must be int, got {type(self.count).__name__}")
        if self.count < 1:
            raise ValueError("count must be >= 1")
        if not isinstance(self.park, bool):
            raise TypeError(f"park must be bool, got {type(self.park).__name__}")
        object.__setattr__(self, "every", every)


@dataclass(frozen=True)
class SetTimeEffect(EffectBase):
    """Set current timezone-aware datetime (simulation handlers may support this effect)."""

    time: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "time", ensure_aware_datetime(self.time, name="time"))


def delay(seconds: float) -> DelayEffect:
    return DelayEffect(seconds=seconds)


def wait_until(target: datetime) -> WaitUntilEffect:
    return WaitUntilEffect(target=target)


def get_time() -> GetTimeEffect:
    return GetTimeEffect()


def get_monotonic() -> GetMonotonicEffect:
    return GetMonotonicEffect()


def schedule_at(time: datetime, program: Any) -> ScheduleAtEffect:
    return ScheduleAtEffect(time=time, program=program)


def set_time(time: datetime) -> SetTimeEffect:
    return SetTimeEffect(time=time)


def wait_within(future: "Future[_T]", seconds: float, *, park: bool = False) -> "WaitWithinEffect[_T]":
    return WaitWithinEffect(future=future, seconds=seconds, park=park)


def Delay(seconds: float) -> EffectBase:  # noqa: N802
    return DelayEffect(seconds=seconds)


def WaitUntil(target: datetime) -> EffectBase:  # noqa: N802
    return WaitUntilEffect(target=target)


def GetTime() -> EffectBase:  # noqa: N802
    return GetTimeEffect()


def GetMonotonic() -> EffectBase:  # noqa: N802
    return GetMonotonicEffect()


def ScheduleAt(time: datetime, program: Any) -> EffectBase:  # noqa: N802
    return ScheduleAtEffect(time=time, program=program)


def SetTime(time: datetime) -> EffectBase:  # noqa: N802
    return SetTimeEffect(time=time)


def WaitWithin(future: "Future[_T]", seconds: float, *, park: bool = False) -> EffectBase:  # noqa: N802
    return WaitWithinEffect(future=future, seconds=seconds, park=park)


# The effects' constructors under the names the other waits use (the classes themselves — a typed answer, no wrapper).
WaitTicks = WaitTicksEffect
RaceWithin = RaceWithinEffect

