"""Spawn handlers."""


from typing import Any

import doeff_vm

from doeff.do import do
from doeff.effects.base import Effect


def _spawn_intercept(effect: Effect, k: Any, handoff: Any):
    from doeff.effects.spawn import SpawnEffect, coerce_task_handle

    @do
    def _program():
        if isinstance(effect, SpawnEffect):
            raw = yield doeff_vm.Delegate()
            return (yield handoff(k, coerce_task_handle(raw)))
        yield doeff_vm.Pass()

    return _program()


@do
def spawn_intercept_handler(effect: Effect, k: Any):
    return (yield _spawn_intercept(effect, k, doeff_vm.Resume))


@do
def sync_spawn_intercept_handler(effect: Effect, k: Any):
    return (yield _spawn_intercept(effect, k, doeff_vm.Transfer))


__all__ = ["spawn_intercept_handler", "sync_spawn_intercept_handler"]
