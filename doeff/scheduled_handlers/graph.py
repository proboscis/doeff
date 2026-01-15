"""Graph tracking effect handlers.

Handlers for GraphStepEffect, GraphAnnotateEffect, GraphSnapshotEffect.
GraphCaptureEffect is a control-flow effect handled in the CESK step function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.runtime import HandlerResult, Resume

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store


def handle_graph_step(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    from doeff._vendor import WGraph, WNode, WStep

    value = effect.value
    meta = effect.meta

    current_graph: WGraph = store.get("__graph__", WGraph.single(None))
    
    input_node = current_graph.last.output
    output_node = WNode(value)
    new_step = WStep(inputs=(input_node,), output=output_node, meta=meta)
    new_steps = current_graph.steps | frozenset({new_step})
    new_graph = WGraph(last=new_step, steps=new_steps)
    new_store = {**store, "__graph__": new_graph}

    return Resume(value, new_store)


def handle_graph_annotate(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    from doeff._vendor import WGraph

    meta = effect.meta
    current_graph: WGraph = store.get("__graph__", WGraph.single(None))

    new_graph = current_graph.with_last_meta(meta)
    new_store = {**store, "__graph__": new_graph}
    return Resume(None, new_store)


def handle_graph_snapshot(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    from doeff._vendor import WGraph

    current_graph: WGraph = store.get("__graph__", WGraph.single(None))
    return Resume(current_graph, store)


__all__ = [
    "handle_graph_step",
    "handle_graph_annotate",
    "handle_graph_snapshot",
]
