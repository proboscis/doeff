"""Asyncio-backed wall-clock handler for doeff-time effects."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from doeff import Await, Effect, Pass, Resume, WithHandler, async_run, default_handlers, do
from doeff_time.effects import DelayEffect, GetTimeEffect, ScheduleAtEffect, WaitUntilEffect

ProtocolHandler = Callable[[Any, Any], Any]


class AsyncTimeRuntime:
    """Runtime container for async wall-clock time effects."""

    def __init__(
        self,
        *,
        now: Callable[[], float],
        sleep: Callable[[float], Awaitable[Any]],
    ) -> None:
        self._now = now
        self._sleep = sleep
        self._pending_tasks: set[asyncio.Task[Any]] = set()

        @do
        def _protocol_handler(effect: Effect, k: Any):
            return (yield self.handle(effect, k))

        self._handler: ProtocolHandler = _protocol_handler

    async def _run_scheduled(self, program: Any) -> None:
        result = await async_run(
            WithHandler(self._handler, program),
            handlers=default_handlers(),
        )
        is_err = getattr(result, "is_err", None)
        if callable(is_err) and is_err():
            error = getattr(result, "error", RuntimeError("scheduled program failed"))
            if isinstance(error, BaseException):
                raise error
            raise RuntimeError(f"scheduled program failed: {error!r}")

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        self._pending_tasks.discard(task)
        if task.cancelled():
            return

        error = task.exception()
        if error is None:
            return

        loop = task.get_loop()
        loop.call_exception_handler(
            {
                "message": "Unhandled exception in scheduled doeff-time async task",
                "exception": error,
                "task": task,
            }
        )

    def _schedule_program(self, program: Any) -> None:
        task = asyncio.create_task(self._run_scheduled(program))
        self._pending_tasks.add(task)
        task.add_done_callback(self._on_task_done)

    @do
    def _handle_delay(self, effect: DelayEffect, k: Any):
        wait_seconds = max(0.0, effect.seconds)
        yield Await(self._sleep(wait_seconds))
        return (yield Resume(k, None))

    @do
    def _handle_wait_until(self, effect: WaitUntilEffect, k: Any):
        wait_seconds = max(0.0, effect.target - self._now())
        yield Await(self._sleep(wait_seconds))
        return (yield Resume(k, None))

    @do
    def _handle_get_time(self, _effect: GetTimeEffect, k: Any):
        return (yield Resume(k, self._now()))

    @do
    def _handle_schedule_at(self, effect: ScheduleAtEffect, k: Any):
        loop = asyncio.get_running_loop()
        wait_seconds = max(0.0, effect.time - self._now())
        loop.call_at(loop.time() + wait_seconds, self._schedule_program, effect.program)
        return (yield Resume(k, None))

    @do
    def handle(self, effect: Effect, k: Any):
        if isinstance(effect, DelayEffect):
            return (yield self._handle_delay(effect, k))
        if isinstance(effect, WaitUntilEffect):
            return (yield self._handle_wait_until(effect, k))
        if isinstance(effect, GetTimeEffect):
            return (yield self._handle_get_time(effect, k))
        if isinstance(effect, ScheduleAtEffect):
            return (yield self._handle_schedule_at(effect, k))
        yield Pass()


def async_time_handler(
    *,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> ProtocolHandler:
    """Return a protocol handler for wall-clock async time semantics."""

    runtime = AsyncTimeRuntime(now=now, sleep=sleep)

    @do
    def handler(effect: Effect, k: Any):
        return (yield runtime._handler(effect, k))

    return handler


__all__ = [
    "ProtocolHandler",
    "async_time_handler",
]
