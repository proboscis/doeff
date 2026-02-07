"""Future/async effects."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any

from ._validators import ensure_awaitable
from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class PythonAsyncioAwaitEffect(EffectBase):
    """Await a Python asyncio awaitable.

    This effect is specifically for Python's asyncio awaitables (coroutines,
    Tasks, Futures). It is NOT a generic "future" abstraction.

    Handled by:
    - python_async_syntax_escape_handler (async_run): produces PythonAsyncSyntaxEscape
    - sync_await_handler (sync_run): runs in background thread

    Usage:
        result = yield Await(some_coroutine())
    """

    awaitable: Awaitable[Any]

    def __post_init__(self) -> None:
        ensure_awaitable(self.awaitable, name="awaitable")


@dataclass(frozen=True)
class AllTasksSuspendedEffect(EffectBase):
    """Signal that all tasks are suspended waiting for I/O.

    Used by the scheduler when all tasks are blocked on async I/O
    and the runtime needs to use asyncio.wait to await them all.
    """

    pending_io: dict[Any, Any]
    store: dict[str, Any]


# NOTE: For parallel execution, use asyncio.create_task + Await + Gather pattern
# See the doeff documentation for examples of concurrent execution patterns


def await_(awaitable: Awaitable[Any]) -> PythonAsyncioAwaitEffect:
    return create_effect_with_trace(PythonAsyncioAwaitEffect(awaitable=awaitable))


def Await(awaitable: Awaitable[Any]) -> Effect:
    return create_effect_with_trace(PythonAsyncioAwaitEffect(awaitable=awaitable), skip_frames=3)


__all__ = [
    "AllTasksSuspendedEffect",
    "Await",
    "PythonAsyncioAwaitEffect",
    "await_",
]
