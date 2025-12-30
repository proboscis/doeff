"""Future/async effects."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable
from typing import Any, Callable

from .base import Effect, EffectBase, create_effect_with_trace
from ._validators import ensure_awaitable


@dataclass(frozen=True)
class FutureAwaitEffect(EffectBase):
    """Awaits the given awaitable and yields its resolved value."""

    awaitable: Awaitable[Any]

    def __post_init__(self) -> None:
        ensure_awaitable(self.awaitable, name="awaitable")

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "FutureAwaitEffect":
        return self


# NOTE: For parallel execution, use asyncio.create_task + Await + Gather pattern
# See the doeff documentation for examples of concurrent execution patterns


def await_(awaitable: Awaitable[Any]) -> FutureAwaitEffect:
    return create_effect_with_trace(FutureAwaitEffect(awaitable=awaitable))


def Await(awaitable: Awaitable[Any]) -> Effect:
    return create_effect_with_trace(FutureAwaitEffect(awaitable=awaitable), skip_frames=3)


__all__ = [
    "FutureAwaitEffect",
    "await_",
    "Await",
]
