from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.runtime import AwaitPayload, HandlerResult, Schedule

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store


def handle_io_perform(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    async def do_async() -> tuple[Any, Store]:
        result = effect.action()
        return (result, store)
    return Schedule(AwaitPayload(do_async()), store)


def handle_io_print(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    async def do_async() -> tuple[Any, Store]:
        print(effect.message)
        return (None, store)
    return Schedule(AwaitPayload(do_async()), store)


__all__ = [
    "handle_io_perform",
    "handle_io_print",
]
