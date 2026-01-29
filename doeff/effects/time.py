"""Unified time effects for all runtimes.

These effects provide runtime-agnostic time control:
- Delay: Wait for a duration
- WaitUntil: Wait until a specific time
- GetTime: Get current time (real or simulated)

Behavior varies by runtime:
- AsyncioRuntime/RealtimeScheduler: Real wall-clock wait
- SimulationScheduler: Instant, advances simulation time

Usage:
    @do
    def my_program():
        yield Delay(seconds=5.0)  # Wait 5 seconds
        yield WaitUntil(datetime(2025, 1, 1, 12, 0, 0))  # Wait until noon
        now = yield GetTime()  # Get current time
        return "done"
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from .base import EffectBase, create_effect_with_trace

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff.types import Effect


@dataclass(frozen=True, kw_only=True)
class DelayEffect(EffectBase):
    """Delay execution for a specified duration.

    In realtime mode (AsyncioRuntime, FIFOScheduler, RealtimeScheduler):
        Actually waits for the duration using asyncio.sleep().

    In simulation mode (SimulationScheduler):
        Instantly advances simulation time by the duration.

    Args:
        seconds: Duration to wait in seconds. Must be non-negative.
    """

    seconds: float

    def __post_init__(self) -> None:
        if self.seconds < 0:
            raise ValueError(f"seconds must be non-negative, got {self.seconds}")

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> DelayEffect:
        return self


@dataclass(frozen=True, kw_only=True)
class WaitUntilEffect(EffectBase):
    """Wait until a specific point in time.

    In realtime mode (AsyncioRuntime, FIFOScheduler, RealtimeScheduler):
        Waits until the wall-clock reaches target_time.
        If target_time is in the past, returns immediately.

    In simulation mode (SimulationScheduler):
        Instantly advances simulation time to target_time.
        If target_time is before current simulation time, returns immediately.

    Args:
        target_time: The datetime to wait until. Should be timezone-aware
            for reliable real-time behavior.
    """

    target_time: datetime

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> WaitUntilEffect:
        return self


@dataclass(frozen=True)
class GetTimeEffect(EffectBase):
    """Get current time.

    In AsyncioRuntime/SyncRuntime: Returns datetime.now()
    In SimulationRuntime: Returns simulated current time
    """

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> GetTimeEffect:
        return self


def Delay(seconds: float) -> Effect:
    """Wait for a specified duration.

    Args:
        seconds: Duration to wait in seconds.

    Returns:
        An effect that, when yielded, causes the program to wait.

    Example:
        @do
        def my_program():
            yield Delay(5.0)  # Wait 5 seconds
            return "done"
    """
    return create_effect_with_trace(
        DelayEffect(seconds=seconds),
        skip_frames=3,
    )


def delay(seconds: float) -> Effect:
    """Wait for a specified duration (lowercase alias).

    Args:
        seconds: Duration to wait in seconds.

    Returns:
        An effect that, when yielded, causes the program to wait.
    """
    return create_effect_with_trace(
        DelayEffect(seconds=seconds),
        skip_frames=3,
    )


def WaitUntil(target_time: datetime) -> Effect:
    """Wait until a specific point in time.

    Args:
        target_time: The datetime to wait until.

    Returns:
        An effect that, when yielded, causes the program to wait.

    Example:
        @do
        def my_program():
            target = datetime(2025, 1, 1, 12, 0, 0)
            yield WaitUntil(target)
            return "arrived at noon"
    """
    return create_effect_with_trace(
        WaitUntilEffect(target_time=target_time),
        skip_frames=3,
    )


def wait_until(target_time: datetime) -> Effect:
    """Wait until a specific point in time (lowercase alias).

    Args:
        target_time: The datetime to wait until.

    Returns:
        An effect that, when yielded, causes the program to wait.
    """
    return create_effect_with_trace(
        WaitUntilEffect(target_time=target_time),
        skip_frames=3,
    )


def GetTime() -> Effect:
    """Get current time.

    Returns:
        An effect that, when yielded, returns the current datetime.
        In SimulationRuntime, returns the simulated time.

    Example:
        @do
        def my_program():
            now = yield GetTime()
            return f"Current time: {now}"
    """
    return create_effect_with_trace(
        GetTimeEffect(),
        skip_frames=3,
    )


def get_time() -> Effect:
    """Get current time (lowercase alias).

    Returns:
        An effect that, when yielded, returns the current datetime.
    """
    return create_effect_with_trace(
        GetTimeEffect(),
        skip_frames=3,
    )


__all__ = [
    "Delay",
    "DelayEffect",
    "GetTime",
    "GetTimeEffect",
    "WaitUntil",
    "WaitUntilEffect",
    "delay",
    "get_time",
    "wait_until",
]
