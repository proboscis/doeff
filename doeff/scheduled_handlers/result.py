from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff._vendor import Err, FrozenDict, Ok
from doeff.runtime import AwaitPayload, HandlerResult, Schedule

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store


def handle_safe(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    from doeff.runtimes import AsyncioRuntime

    async def run_safe() -> tuple[Any, Store]:
        runtime = AsyncioRuntime()
        dispatcher = store.get("__dispatcher__")
        E = FrozenDict(env) if not isinstance(env, FrozenDict) else env
        child_store = {**store}
        if dispatcher is not None:
            child_store["__dispatcher__"] = dispatcher

        try:
            runtime_result = await runtime.run_safe(effect.sub_program, E, child_store)
            final_store = runtime_result.final_store or child_store
            parent_log = store.get("__log__", [])
            child_log = final_store.get("__log__", [])
            merged_store = {
                **store,
                **{k: v for k, v in final_store.items() if not k.startswith("__")},
            }
            merged_store["__log__"] = parent_log + child_log[len(parent_log):]
            return (Ok(runtime_result.result), merged_store)
        except Exception as e:
            return (Err(e), store)

    return Schedule(AwaitPayload(run_safe()), store)


__all__ = ["handle_safe"]
