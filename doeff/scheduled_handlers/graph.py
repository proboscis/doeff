from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff._vendor import FrozenDict, WGraph, WNode, WStep
from doeff.runtime import AwaitPayload, HandlerResult, Resume, Schedule

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store


def handle_graph_step(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    current_graph = store.get("__graph__", WGraph.single(None))
    node = WNode(effect.value)
    step = WStep(inputs=(current_graph.last.output,), output=node, meta=dict(effect.meta))
    new_graph = WGraph(last=step, steps=current_graph.steps | {step})
    new_store = {**store, "__graph__": new_graph}
    return Resume(effect.value, new_store)


def handle_graph_annotate(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    current_graph = store.get("__graph__", WGraph.single(None))
    new_graph = current_graph.with_last_meta(dict(effect.meta))
    new_store = {**store, "__graph__": new_graph}
    return Resume(None, new_store)


def handle_graph_snapshot(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    current_graph = store.get("__graph__", WGraph.single(None))
    return Resume(current_graph, store)


def handle_graph_capture(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    from doeff.runtimes import AsyncioRuntime

    async def run_capture() -> tuple[Any, Store]:
        runtime = AsyncioRuntime()
        dispatcher = store.get("__dispatcher__")
        E = FrozenDict(env) if not isinstance(env, FrozenDict) else env
        child_store = {**store, "__graph__": WGraph.single(None)}
        if dispatcher is not None:
            child_store["__dispatcher__"] = dispatcher

        runtime_result = await runtime.run_safe(effect.program, E, child_store)
        final_store = runtime_result.final_store or child_store
        captured_graph = final_store.get("__graph__", WGraph.single(None))
        parent_log = store.get("__log__", [])
        child_log = final_store.get("__log__", [])
        merged_store = {
            **store,
            **{k: v for k, v in final_store.items() if not k.startswith("__")},
        }
        merged_store["__log__"] = parent_log + child_log[len(parent_log):]
        result = (runtime_result.result, captured_graph)
        return (result, merged_store)

    return Schedule(AwaitPayload(run_capture()), store)


__all__ = [
    "handle_graph_step",
    "handle_graph_annotate",
    "handle_graph_snapshot",
    "handle_graph_capture",
]
