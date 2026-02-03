"""Graph effect handler for GraphStep, GraphAnnotate, GraphSnapshot effects.

Note: GraphCapture is handled by core_handler because it nests a program
and requires access to the full continuation without forwarding pollution.
"""

from __future__ import annotations

from doeff.do import do
from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState
from doeff.effects.graph import (
    GraphAnnotateEffect,
    GraphSnapshotEffect,
    GraphStepEffect,
)


@do
def graph_handler(effect: EffectBase, ctx: HandlerContext):
    store = dict(ctx.store)

    if isinstance(effect, GraphStepEffect):
        graph = store.get("__graph__", [])
        node = {"value": effect.value, "meta": effect.meta}
        new_graph = graph + [node]
        new_store = {**store, "__graph__": new_graph}
        return CESKState.with_value(effect.value, ctx.env, new_store, ctx.k)

    if isinstance(effect, GraphAnnotateEffect):
        graph = store.get("__graph__", [])
        if graph:
            last_node = graph[-1]
            updated_node = {**last_node, "meta": {**last_node.get("meta", {}), **effect.meta}}
            new_graph = graph[:-1] + [updated_node]
            new_store = {**store, "__graph__": new_graph}
        else:
            new_store = store
        return CESKState.with_value(None, ctx.env, new_store, ctx.k)

    if isinstance(effect, GraphSnapshotEffect):
        graph = store.get("__graph__", [])
        return CESKState.with_value(list(graph), ctx.env, store, ctx.k)

    result = yield effect
    return result


__all__ = ["graph_handler"]
