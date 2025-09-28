"""Future/async effects."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable
from typing import Any, Callable, Tuple

from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class FutureAwaitEffect(EffectBase):
    """Awaits the given awaitable and yields its resolved value."""

    awaitable: Awaitable[Any]

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "FutureAwaitEffect":
        return self


@dataclass(frozen=True)
class FutureParallelEffect(EffectBase):
    """Runs all awaitables concurrently and yields the collected results list."""

    awaitables: Tuple[Awaitable[Any], ...]

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "FutureParallelEffect":
        return self


def await_(awaitable: Awaitable[Any]) -> FutureAwaitEffect:
    return create_effect_with_trace(FutureAwaitEffect(awaitable=awaitable))


def parallel(*awaitables: Awaitable[Any]) -> FutureParallelEffect:
    return create_effect_with_trace(
        FutureParallelEffect(awaitables=tuple(awaitables))
    )


def Await(awaitable: Awaitable[Any]) -> Effect:
    return create_effect_with_trace(FutureAwaitEffect(awaitable=awaitable), skip_frames=3)


def Parallel(*awaitables: Awaitable[Any]) -> Effect:
    return create_effect_with_trace(
        FutureParallelEffect(awaitables=tuple(awaitables)), skip_frames=3
    )


__all__ = [
    "FutureAwaitEffect",
    "FutureParallelEffect",
    "await_",
    "parallel",
    "Await",
    "Parallel",
]
