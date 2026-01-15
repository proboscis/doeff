"""Reader effect handlers.

Direct ScheduledEffectHandler implementation for AskEffect and LocalEffect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff._vendor import FrozenDict
from doeff.runtime import AwaitPayload, HandlerResult, Resume, Schedule

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store


def handle_ask(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    if effect.key not in env:
        raise KeyError(f"Missing environment key: {effect.key!r}")
    value = env[effect.key]
    return Resume(value, store)


def handle_local(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    from doeff.runtimes import AsyncioRuntime

    updated_env = FrozenDict({**dict(env), **dict(effect.env_update)})

    async def run_local() -> tuple[Any, Store]:
        runtime = AsyncioRuntime()
        dispatcher = store.get("__dispatcher__")
        local_store = {**store}
        if dispatcher is not None:
            local_store["__dispatcher__"] = dispatcher

        runtime_result = await runtime.run_safe(
            effect.sub_program, updated_env, local_store
        )
        # Preserve log entries from sub-program
        final_store = runtime_result.final_store or store
        parent_log = store.get("__log__", [])
        child_log = final_store.get("__log__", [])
        merged_store = {**store, **{k: v for k, v in final_store.items() if not k.startswith("__")}}
        merged_store["__log__"] = parent_log + child_log[len(parent_log):]
        return (runtime_result.result, merged_store)

    return Schedule(AwaitPayload(run_local()), store)


__all__ = [
    "handle_ask",
    "handle_local",
]
