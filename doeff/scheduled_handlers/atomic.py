"""Atomic effect handlers for the CESK interpreter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.runtime import HandlerResult, Resume

if TYPE_CHECKING:
    from doeff.cesk.types import Environment, Store
    from doeff.effects.atomic import AtomicGetEffect, AtomicUpdateEffect


def handle_atomic_get(
    effect: "AtomicGetEffect",
    env: "Environment",
    store: "Store",
) -> HandlerResult:
    key = f"__atomic__{effect.key}"
    if key in store:
        return Resume(value=store[key], store=store)
    if effect.default_factory is not None:
        default = effect.default_factory()
        new_store = {**store, key: default}
        return Resume(value=default, store=new_store)
    raise KeyError(f"Atomic key not found: {effect.key}")


def handle_atomic_update(
    effect: "AtomicUpdateEffect",
    env: "Environment",
    store: "Store",
) -> HandlerResult:
    key = f"__atomic__{effect.key}"
    if key in store:
        current = store[key]
    elif effect.default_factory is not None:
        current = effect.default_factory()
    else:
        current = None
    new_value = effect.updater(current)
    new_store = {**store, key: new_value}
    return Resume(value=new_value, store=new_store)


__all__ = [
    "handle_atomic_get",
    "handle_atomic_update",
]
