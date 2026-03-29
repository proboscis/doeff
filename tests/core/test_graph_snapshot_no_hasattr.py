"""Regression coverage for graph snapshot typing and structure."""

import inspect

from doeff import graph_snapshot
# REMOVED: from doeff._vendor import WGraph, WNode, WStep


def test_build_graph_snapshot_does_not_use_hasattr() -> None:
    source = inspect.getsource(graph_snapshot.build_graph_snapshot)
    assert "hasattr(" not in source


def test_build_graph_snapshot_builds_nodes_and_edges() -> None:
    first_node = WNode("seed")
    second_node = WNode("result")
    first_step = WStep(inputs=(), output=first_node, meta={"label": "Seed"})
    second_step = WStep(inputs=(first_node,), output=second_node, meta={"label": "Result"})
    graph = WGraph(last=second_step, steps=frozenset({first_step, second_step}))

    snapshot = graph_snapshot.build_graph_snapshot(graph, mark_success=True)

    assert len(snapshot["nodes"]) == 2
    assert len(snapshot["edges"]) == 1
    assert snapshot["edges"][0]["arrows"] == "to"
