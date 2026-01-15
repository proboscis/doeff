"""Atomic shared-state effect handlers.

Handlers for AtomicGetEffect and AtomicUpdateEffect.
Atomic state is stored in store['__atomic_state__'] as a dict.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from doeff.runtime import HandlerResult, Resume

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store

_atomic_lock = threading.Lock()


def handle_atomic_get(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    key = effect.key
    default_factory = effect.default_factory

    with _atomic_lock:
        atomic_state = store.get("__atomic_state__", {})

        if key not in atomic_state:
            if default_factory is not None:
                value = default_factory()
                new_atomic_state = {**atomic_state, key: value}
                new_store = {**store, "__atomic_state__": new_atomic_state}
                return Resume(value, new_store)
            return Resume(None, store)

        return Resume(atomic_state[key], store)


def handle_atomic_update(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    key = effect.key
    updater = effect.updater
    default_factory = effect.default_factory

    with _atomic_lock:
        atomic_state = store.get("__atomic_state__", {})

        if key not in atomic_state:
            if default_factory is not None:
                current_value = default_factory()
            else:
                current_value = None
        else:
            current_value = atomic_state[key]

        new_value = updater(current_value)
        new_atomic_state = {**atomic_state, key: new_value}
        new_store = {**store, "__atomic_state__": new_atomic_state}

        return Resume(new_value, new_store)


__all__ = [
    "handle_atomic_get",
    "handle_atomic_update",
]
