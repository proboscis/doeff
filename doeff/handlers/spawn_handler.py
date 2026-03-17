"""Spawn handlers."""


from typing import Any

import doeff_vm

from doeff.do import do
from doeff.effects.base import Effect


@do
def spawn_intercept_handler(effect: Effect, k: Any):
    from doeff.effects.spawn import SpawnEffect, coerce_task_handle

    if isinstance(effect, SpawnEffect):
        raw = yield doeff_vm.Delegate()
        return (yield doeff_vm.Resume(k, coerce_task_handle(raw)))
    yield doeff_vm.Pass()


@do
def sync_spawn_intercept_handler(effect: Effect, k: Any):
    from doeff.effects.spawn import SpawnEffect, coerce_task_handle

    if isinstance(effect, SpawnEffect):
        raw = yield doeff_vm.Delegate()
        return (yield doeff_vm.Transfer(k, coerce_task_handle(raw)))
    yield doeff_vm.Pass()


__all__ = ["spawn_intercept_handler", "sync_spawn_intercept_handler"]
