"""Graph tracking effect handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.frames import ContinueProgram, ContinueValue, FrameResult
from doeff.cesk.types import Store
from doeff.effects.graph import (
    GraphAnnotateEffect,
    GraphCaptureEffect,
    GraphSnapshotEffect,
    GraphStepEffect,
)

if TYPE_CHECKING:
    from doeff.cesk.runtime.context import HandlerContext


def _get_graph(store: Store) -> list[dict[str, Any]]:
    return store.get("__graph__", [])


def _set_graph(store: Store, graph: list[dict[str, Any]]) -> Store:
    return {**store, "__graph__": graph}


def handle_graph_step(
    effect: GraphStepEffect,
    ctx: HandlerContext,
) -> FrameResult:
    graph = _get_graph(ctx.store)
    node = {"value": effect.value, "meta": effect.meta}
    new_graph = graph + [node]
    new_store = _set_graph(ctx.store, new_graph)
    return ContinueValue(
        value=effect.value,
        env=ctx.task_state.env,
        store=new_store,
        k=ctx.task_state.kontinuation,
    )


def handle_graph_annotate(
    effect: GraphAnnotateEffect,
    ctx: HandlerContext,
) -> FrameResult:
    graph = _get_graph(ctx.store)
    if graph:
        last_node = graph[-1]
        updated_node = {**last_node, "meta": {**last_node.get("meta", {}), **effect.meta}}
        new_graph = graph[:-1] + [updated_node]
        new_store = _set_graph(ctx.store, new_graph)
    else:
        new_store = ctx.store
    return ContinueValue(
        value=None,
        env=ctx.task_state.env,
        store=new_store,
        k=ctx.task_state.kontinuation,
    )


def handle_graph_snapshot(
    effect: GraphSnapshotEffect,
    ctx: HandlerContext,
) -> FrameResult:
    graph = _get_graph(ctx.store)
    return ContinueValue(
        value=list(graph),
        env=ctx.task_state.env,
        store=ctx.store,
        k=ctx.task_state.kontinuation,
    )


def handle_graph_capture(
    effect: GraphCaptureEffect,
    ctx: HandlerContext,
) -> FrameResult:
    from doeff.cesk.frames import GraphCaptureFrame

    graph_start = len(_get_graph(ctx.store))
    return ContinueProgram(
        program=effect.program,
        env=ctx.task_state.env,
        store=ctx.store,
        k=[GraphCaptureFrame(graph_start)] + ctx.task_state.kontinuation,
    )


__all__ = [
    "handle_graph_annotate",
    "handle_graph_capture",
    "handle_graph_snapshot",
    "handle_graph_step",
]
