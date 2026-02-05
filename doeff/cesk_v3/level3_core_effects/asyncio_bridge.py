from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, TypeVar

from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase
from doeff.cesk_v3.level2_algebraic_effects.primitives import (
    Forward,
    PythonAsyncSyntaxEscape,
    Resume,
)
from doeff.do import do
from doeff.program import Program

T = TypeVar("T")


@dataclass(frozen=True)
class AwaitEffect(EffectBase):
    awaitable: Awaitable[Any]


def Await(awaitable: Awaitable[T]) -> AwaitEffect:
    return AwaitEffect(awaitable=awaitable)


def sync_await_handler(effect: EffectBase) -> Program[Any]:
    @do
    def handler(eff: EffectBase) -> Program[Any]:
        if isinstance(eff, AwaitEffect):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_awaitable_sync, eff.awaitable)
                result = future.result()
            return (yield Resume(result))
        forwarded = yield Forward(eff)
        return (yield Resume(forwarded))

    return handler(effect)


def _run_awaitable_sync(awaitable: Awaitable[Any]) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(awaitable)
    finally:
        loop.close()


def python_async_syntax_escape_handler(effect: EffectBase) -> Program[Any]:
    @do
    def handler(eff: EffectBase) -> Program[Any]:
        if isinstance(eff, AwaitEffect):

            async def run_awaitable() -> Any:
                return await eff.awaitable

            awaited_value = yield PythonAsyncSyntaxEscape(action=run_awaitable)
            return (yield Resume(awaited_value))
        forwarded = yield Forward(eff)
        return (yield Resume(forwarded))

    return handler(effect)


__all__ = [
    "Await",
    "AwaitEffect",
    "python_async_syntax_escape_handler",
    "sync_await_handler",
]
