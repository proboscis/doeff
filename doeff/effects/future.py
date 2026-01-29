"""Future/async effects."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any

from ._validators import ensure_awaitable
from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class FutureAwaitEffect(EffectBase):
    """Awaits the given awaitable and yields its resolved value."""

    awaitable: Awaitable[Any]

    def __post_init__(self) -> None:
        ensure_awaitable(self.awaitable, name="awaitable")


# NOTE: For parallel execution, use asyncio.create_task + Await + Gather pattern
# See the doeff documentation for examples of concurrent execution patterns


def await_(awaitable: Awaitable[Any]) -> FutureAwaitEffect:
    return create_effect_with_trace(FutureAwaitEffect(awaitable=awaitable))


def Await(awaitable: Awaitable[Any]) -> Effect:
    return create_effect_with_trace(FutureAwaitEffect(awaitable=awaitable), skip_frames=3)


__all__ = [
    "Await",
    "FutureAwaitEffect",
    "await_",
]
