"""Memo effect handlers.

Direct ScheduledEffectHandler implementations for MemoGetEffect and MemoPutEffect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.runtime import HandlerResult, Resume

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store
    from doeff.runtime import Continuation, Scheduler


def handle_memo_get(
    effect: EffectBase,
    env: Environment,
    store: Store,
    k: Continuation,
    scheduler: Scheduler | None,
) -> HandlerResult:
    """Handle MemoGetEffect - retrieves memoized value by key."""
    memo = store.get("__memo__", {})
    value = memo.get(effect.key)
    return Resume(value, store)


def handle_memo_put(
    effect: EffectBase,
    env: Environment,
    store: Store,
    k: Continuation,
    scheduler: Scheduler | None,
) -> HandlerResult:
    """Handle MemoPutEffect - stores memoized value by key."""
    memo = {**store.get("__memo__", {}), effect.key: effect.value}
    new_store = {**store, "__memo__": memo}
    return Resume(None, new_store)


__all__ = [
    "handle_memo_get",
    "handle_memo_put",
]
