"""Graph effect handlers for the CESK interpreter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.runtime import HandlerResult, Resume

if TYPE_CHECKING:
    from doeff.cesk.types import Environment, Store
    from doeff.effects.graph import GraphAnnotateEffect, GraphCaptureEffect, GraphSnapshotEffect, GraphStepEffect


def handle_graph_step(
    effect: "GraphStepEffect",
    env: "Environment",
    store: "Store",
) -> HandlerResult:
    return Resume(value=effect.value, store=store)


def handle_graph_annotate(
    effect: "GraphAnnotateEffect",
    env: "Environment",
    store: "Store",
) -> HandlerResult:
    return Resume(value=None, store=store)


def handle_graph_capture(
    effect: "GraphCaptureEffect",
    env: "Environment",
    store: "Store",
) -> HandlerResult:
    from doeff._vendor import WGraph
    return Resume(value=WGraph.single(None), store=store)


def handle_graph_snapshot(
    effect: "GraphSnapshotEffect",
    env: "Environment",
    store: "Store",
) -> HandlerResult:
    from doeff._vendor import WGraph
    return Resume(value=WGraph.single(None), store=store)


__all__ = [
    "handle_graph_step",
    "handle_graph_annotate",
    "handle_graph_capture",
    "handle_graph_snapshot",
]
