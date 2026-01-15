"""Concurrency effect handlers."""

from __future__ import annotations

import asyncio
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from doeff._vendor import Err, Ok, Result, FrozenDict
from doeff.runtime import AwaitPayload, HandlerResult, Schedule

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store


_shared_executor: ThreadPoolExecutor | None = None
_shared_executor_lock = threading.Lock()


def _get_shared_executor() -> ThreadPoolExecutor:
    global _shared_executor
    if _shared_executor is None:
        with _shared_executor_lock:
            if _shared_executor is None:
                _shared_executor = ThreadPoolExecutor(
                    max_workers=4,
                    thread_name_prefix="cesk-pooled",
                )
    return _shared_executor


def _merge_store(parent_store: Store, child_store: Store) -> Store:
    merged = {**parent_store}

    for key, value in child_store.items():
        if key.startswith("__"):
            continue
        if key not in parent_store:
            merged[key] = value

    parent_log = merged.get("__log__", [])
    child_log = child_store.get("__log__", [])
    merged["__log__"] = parent_log + child_log

    parent_memo = merged.get("__memo__", {})
    child_memo = child_store.get("__memo__", {})
    merged["__memo__"] = {**parent_memo, **child_memo}

    return merged


async def _run_program_internal(program, env, store, dispatcher=None):
    from doeff.runtimes import AsyncioRuntime
    
    E = FrozenDict(env) if not isinstance(env, FrozenDict) else env
    
    runtime = AsyncioRuntime()
    if dispatcher is not None:
        store = {**store, "__dispatcher__": dispatcher}
    
    runtime_result = await runtime.run_safe(program, E, store)
    return runtime_result.result, runtime_result.final_store or store


def handle_future_await(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    async def do_async() -> tuple[Any, Store]:
        result = await effect.awaitable
        return (result, store)
    return Schedule(AwaitPayload(do_async()), store)


def handle_spawn(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    from doeff.effects.spawn import Task

    parent_dispatcher = store.get("__dispatcher__")

    store_without_dispatcher = {key: v for key, v in store.items() if key != "__dispatcher__"}
    child_store = copy.deepcopy(store_without_dispatcher)
    child_env = env

    final_store_holder: dict[str, Any] = {"store": None}

    async def run_and_capture_store():
        result, final_store = await _run_program_internal(
            effect.program, child_env, child_store, dispatcher=parent_dispatcher
        )
        final_store_holder["store"] = final_store
        return result

    async def do_async() -> tuple[Any, Store]:
        async_task = asyncio.create_task(run_and_capture_store())

        task = Task(
            backend=effect.preferred_backend or "thread",
            _handle=async_task,
            _env_snapshot=dict(env),
            _state_snapshot=final_store_holder,
        )
        return (task, store)

    return Schedule(AwaitPayload(do_async()), store)


def handle_task_join(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    async def do_async() -> tuple[Any, Store]:
        task = effect.task
        if hasattr(task, "_handle") and isinstance(task._handle, asyncio.Task):
            result = await task._handle

            if isinstance(result, Err):
                raise result.error

            final_store_holder = task._state_snapshot
            if isinstance(final_store_holder, dict) and "store" in final_store_holder:
                child_final_store = final_store_holder.pop("store", None)
                if child_final_store is not None:
                    merged_store = _merge_store(store, child_final_store)
                else:
                    merged_store = store
            else:
                merged_store = store

            if isinstance(result, Ok):
                return (result.value, merged_store)
            return (result, merged_store)
        raise ValueError(f"Cannot join task with handle type: {type(task._handle)}")

    return Schedule(AwaitPayload(do_async()), store)


def handle_spawn_scheduled(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    from doeff.runtime import SpawnPayload
    store_without_dispatcher = {key: v for key, v in store.items() if key != "__dispatcher__"}
    child_store = copy.deepcopy(store_without_dispatcher)
    return Schedule(SpawnPayload(program=effect.program, env=env, store=child_store), store)


def handle_gather(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    parent_dispatcher = store.get("__dispatcher__")

    async def run_gather() -> tuple[Any, Store]:
        E = FrozenDict(env) if not isinstance(env, FrozenDict) else env
        tasks = []
        for program in effect.programs:
            store_copy = copy.deepcopy({k: v for k, v in store.items() if k != "__dispatcher__"})
            tasks.append(_run_program_internal(program, E, store_copy, dispatcher=parent_dispatcher))

        results_with_stores = await asyncio.gather(*tasks)
        results = [r for r, _ in results_with_stores]
        merged_store = store
        for _, child_store in results_with_stores:
            merged_store = _merge_store(merged_store, child_store)
        return (results, merged_store)

    return Schedule(AwaitPayload(run_gather()), store)


__all__ = [
    "handle_future_await",
    "handle_spawn",
    "handle_spawn_scheduled",
    "handle_task_join",
    "handle_gather",
    "_get_shared_executor",
]
