"""Graph tracking effect handlers."""

from __future__ import annotations

from typing import Any

from doeff.cesk.frames import ContinueProgram, ContinueValue, FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store
from doeff.effects.graph import (
    GraphAnnotateEffect,
    GraphCaptureEffect,
    GraphSnapshotEffect,
    GraphStepEffect,
)


def _get_graph(store: Store) -> list[dict[str, Any]]:
    return store.get("__graph__", [])


def _set_graph(store: Store, graph: list[dict[str, Any]]) -> Store:
    return {**store, "__graph__": graph}


def handle_graph_step(
    effect: GraphStepEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    graph = _get_graph(store)
    node = {"value": effect.value, "meta": effect.meta}
    new_graph = graph + [node]
    new_store = _set_graph(store, new_graph)
    return ContinueValue(
        value=effect.value,
        env=task_state.env,
        store=new_store,
        k=task_state.kontinuation,
    )


def handle_graph_annotate(
    effect: GraphAnnotateEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    graph = _get_graph(store)
    if graph:
        last_node = graph[-1]
        updated_node = {**last_node, "meta": {**last_node.get("meta", {}), **effect.meta}}
        new_graph = graph[:-1] + [updated_node]
        new_store = _set_graph(store, new_graph)
    else:
        new_store = store
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=new_store,
        k=task_state.kontinuation,
    )


def handle_graph_snapshot(
    effect: GraphSnapshotEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    graph = _get_graph(store)
    return ContinueValue(
        value=list(graph),
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_graph_capture(
    effect: GraphCaptureEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    from doeff.cesk.frames import GraphCaptureFrame

    graph_start = len(_get_graph(store))
    return ContinueProgram(
        program=effect.program,
        env=task_state.env,
        store=store,
        k=[GraphCaptureFrame(graph_start)] + task_state.kontinuation,
    )


__all__ = [
    "handle_graph_annotate",
    "handle_graph_capture",
    "handle_graph_snapshot",
    "handle_graph_step",
]
