"""Writer effect handlers.

Direct ScheduledEffectHandler implementation for WriterTellEffect and WriterListenEffect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff._vendor import FrozenDict
from doeff.runtime import AwaitPayload, HandlerResult, Resume, Schedule
from doeff.utils import BoundedLog

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store


def handle_writer_tell(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    log = store.get("__log__", [])
    new_log = log + [effect.message]
    new_store = {**store, "__log__": new_log}
    return Resume(None, new_store)


def handle_writer_listen(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    from doeff._types_internal import ListenResult
    from doeff.runtimes import AsyncioRuntime

    async def run_listen() -> tuple[Any, Store]:
        runtime = AsyncioRuntime()
        dispatcher = store.get("__dispatcher__")
        E = FrozenDict(env) if not isinstance(env, FrozenDict) else env
        child_store = {**store, "__log__": []}
        if dispatcher is not None:
            child_store["__dispatcher__"] = dispatcher

        runtime_result = await runtime.run_safe(effect.sub_program, E, child_store)
        final_store = runtime_result.final_store or child_store
        captured_log = BoundedLog(final_store.get("__log__", []))
        parent_log = store.get("__log__", [])
        merged_store = {
            **store,
            **{k: v for k, v in final_store.items() if not k.startswith("__")},
        }
        merged_store["__log__"] = parent_log + list(captured_log)
        result = ListenResult(value=runtime_result.result, log=captured_log)
        return (result, merged_store)

    return Schedule(AwaitPayload(run_listen()), store)


__all__ = [
    "handle_writer_tell",
    "handle_writer_listen",
]
