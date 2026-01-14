"""State effect handlers.

Direct ScheduledEffectHandler implementations for StateGetEffect,
StatePutEffect, and StateModifyEffect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.runtime import HandlerResult, Resume

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store


def handle_state_get(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    value = store.get(effect.key)
    return Resume(value, store)


def handle_state_put(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    new_store = {**store, effect.key: effect.value}
    return Resume(None, new_store)


def handle_state_modify(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    old_value = store.get(effect.key)
    new_value = effect.func(old_value)
    new_store = {**store, effect.key: new_value}
    return Resume(new_value, new_store)


__all__ = [
    "handle_state_get",
    "handle_state_put",
    "handle_state_modify",
]
