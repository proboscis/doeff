"""IO effect handlers.

Direct ScheduledEffectHandler implementations for IOPerformEffect and IOPrintEffect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.runtime import HandlerResult, Suspend

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store
    from doeff.runtime import Continuation, Scheduler


def handle_io_perform(
    effect: EffectBase,
    env: Environment,
    store: Store,
    k: Continuation,
    scheduler: Scheduler | None,
) -> HandlerResult:
    """Handle IOPerformEffect - executes an IO action."""
    async def do_async() -> tuple[Any, Store]:
        result = effect.action()
        return (result, store)
    return Suspend(do_async(), store)


def handle_io_print(
    effect: EffectBase,
    env: Environment,
    store: Store,
    k: Continuation,
    scheduler: Scheduler | None,
) -> HandlerResult:
    """Handle IOPrintEffect - prints a message to stdout."""
    async def do_async() -> tuple[Any, Store]:
        print(effect.message)
        return (None, store)
    return Suspend(do_async(), store)


__all__ = [
    "handle_io_perform",
    "handle_io_print",
]
