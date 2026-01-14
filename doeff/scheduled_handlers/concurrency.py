"""Concurrency effect handlers.

Direct ScheduledEffectHandler implementations for FutureAwaitEffect,
SpawnEffect, ThreadEffect, and TaskJoinEffect.
"""

from __future__ import annotations

import asyncio
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from doeff._vendor import Err, Ok, Result
from doeff.runtime import HandlerResult, Schedule

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store


# ============================================================================
# Thread Pool Management
# ============================================================================

_shared_executor: ThreadPoolExecutor | None = None
_shared_executor_lock = threading.Lock()


def _get_shared_executor() -> ThreadPoolExecutor:
    """Get or create the shared thread pool executor for 'pooled' strategy."""
    global _shared_executor
    if _shared_executor is None:
        with _shared_executor_lock:
            if _shared_executor is None:
                _shared_executor = ThreadPoolExecutor(
                    max_workers=4,
                    thread_name_prefix="cesk-pooled",
                )
    return _shared_executor


# ============================================================================
# State Merging Helpers
# ============================================================================


def _merge_store(parent_store: Store, child_store: Store) -> Store:
    """Merge child store into parent after child completion."""
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


def _merge_thread_state(parent_store: Store, child_store: Store) -> Store:
    """Merge thread state: child state replaces parent (except logs append)."""
    merged = {}

    for key, value in child_store.items():
        if not key.startswith("__"):
            merged[key] = value
    for key, value in parent_store.items():
        if not key.startswith("__") and key not in merged:
            merged[key] = value

    parent_log = parent_store.get("__log__", [])
    child_log = child_store.get("__log__", [])
    if child_log:
        merged["__log__"] = list(parent_log) + list(child_log)
    elif parent_log:
        merged["__log__"] = list(parent_log)

    parent_memo = parent_store.get("__memo__", {})
    child_memo = child_store.get("__memo__", {})
    if parent_memo or child_memo:
        merged["__memo__"] = {**parent_memo, **child_memo}

    if "__durable_storage__" in parent_store:
        merged["__durable_storage__"] = parent_store["__durable_storage__"]

    return merged


# ============================================================================
# Effect Handlers
# ============================================================================


def handle_future_await(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    async def do_async() -> tuple[Any, Store]:
        result = await effect.awaitable
        return (result, store)
    return Schedule(do_async(), store)


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
        from doeff.cesk import _run_internal
        result, final_store, _ = await _run_internal(
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

    return Schedule(do_async(), store)


def handle_thread(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    parent_dispatcher = store.get("__dispatcher__")

    store_without_dispatcher = {key: v for key, v in store.items() if key != "__dispatcher__"}
    child_store = copy.deepcopy(store_without_dispatcher)
    child_env = env
    strategy = effect.strategy

    async def do_async() -> tuple[Any, Store]:
        from doeff.cesk import _run_internal
        loop = asyncio.get_running_loop()

        def run_in_thread() -> tuple[Result, Store]:
            thread_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(thread_loop)
            try:
                result, final_store, _ = thread_loop.run_until_complete(
                    _run_internal(effect.program, child_env, child_store, dispatcher=parent_dispatcher)
                )
                return result, final_store
            finally:
                thread_loop.close()

        if strategy == "pooled":
            executor = _get_shared_executor()

            if effect.await_result:
                result, child_final_store = await loop.run_in_executor(executor, run_in_thread)
                if isinstance(result, Ok):
                    merged_store = _merge_thread_state(store, child_final_store)
                    return (result.value, merged_store)
                if isinstance(result, Err):
                    raise result.error
                return (result, store)

            raw_future = loop.run_in_executor(executor, run_in_thread)

            async def unwrap_thread_result():
                result, _ = await raw_future
                if isinstance(result, Ok):
                    return result.value
                if isinstance(result, Err):
                    raise result.error
                return result

            return (unwrap_thread_result(), store)

        is_daemon = strategy == "daemon"
        future: asyncio.Future[tuple[Result, Store]] = loop.create_future()

        def thread_target() -> None:
            try:
                result = run_in_thread()
            except BaseException as exc:
                loop.call_soon_threadsafe(future.set_exception, exc)
            else:
                loop.call_soon_threadsafe(future.set_result, result)

        thread = threading.Thread(
            target=thread_target,
            name=f"cesk-{'daemon' if is_daemon else 'dedicated'}",
            daemon=is_daemon,
        )
        thread.start()

        if effect.await_result:
            result, child_final_store = await future
            if isinstance(result, Ok):
                merged_store = _merge_thread_state(store, child_final_store)
                return (result.value, merged_store)
            if isinstance(result, Err):
                raise result.error
            return (result, store)

        async def unwrap_thread_result():
            result, _ = await future
            if isinstance(result, Ok):
                return result.value
            if isinstance(result, Err):
                raise result.error
            return result

        return (unwrap_thread_result(), store)

    return Schedule(do_async(), store)


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

    return Schedule(do_async(), store)


__all__ = [
    "handle_future_await",
    "handle_spawn",
    "handle_thread",
    "handle_task_join",
    "_get_shared_executor",
]
