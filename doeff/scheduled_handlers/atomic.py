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
    atomic_state = store.get("__atomic__", {})
    key = effect.key
    with _atomic_lock:
        if key in atomic_state:
            value = atomic_state[key]
        elif effect.default_factory is not None:
            value = effect.default_factory()
            atomic_state = {**atomic_state, key: value}
        else:
            raise KeyError(f"Atomic key not found: {key!r}")
    new_store = {**store, "__atomic__": atomic_state}
    return Resume(value, new_store)


def handle_atomic_update(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    atomic_state = store.get("__atomic__", {})
    key = effect.key
    with _atomic_lock:
        if key in atomic_state:
            old_value = atomic_state[key]
        elif effect.default_factory is not None:
            old_value = effect.default_factory()
        else:
            raise KeyError(f"Atomic key not found: {key!r}")
        new_value = effect.updater(old_value)
        atomic_state = {**atomic_state, key: new_value}
    new_store = {**store, "__atomic__": atomic_state}
    return Resume(new_value, new_store)


__all__ = ["handle_atomic_get", "handle_atomic_update"]
