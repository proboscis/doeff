import pytest

from doeff._vendor import WGraph, WNode, WStep
from doeff.webui_stream import GraphEffectReporter, GraphEventStream
from _webui_snapshot import build_snapshot_html


def _build_test_graph() -> WGraph:
    steps: list[WStep] = []

    bases = [WNode(f"input_{i}") for i in range(3)]
    for node in bases:
        steps.append(WStep((), node))

    node_l1_a = WNode("l1_a")
    steps.append(WStep((bases[0], bases[1]), node_l1_a))
    node_l1_b = WNode("l1_b")
    steps.append(WStep((bases[2],), node_l1_b))
    node_l1_c = WNode("l1_c")
    steps.append(WStep((bases[1],), node_l1_c))

    node_l2_a = WNode("l2_a")
    steps.append(WStep((node_l1_a, node_l1_b), node_l2_a))
    node_l2_b = WNode("l2_b")
    steps.append(WStep((node_l1_b, node_l1_c), node_l2_b))
    node_l2_c = WNode("l2_c")
    steps.append(WStep((bases[0], node_l1_c), node_l2_c))

    node_l3_a = WNode("l3_a")
    steps.append(WStep((node_l2_a, node_l2_b), node_l3_a))
    node_l3_b = WNode("l3_b")
    steps.append(WStep((node_l2_a, node_l2_c), node_l3_b))

    node_final = WNode("final")
    final_step = WStep((node_l3_a, node_l3_b, node_l1_a), node_final)

    all_steps = frozenset(steps + [final_step])
    return WGraph(last=final_step, steps=all_steps)


@pytest.mark.asyncio
async def test_rust_snapshot_matches_graph_and_html():
    graph = _build_test_graph()

    expected_nodes = {step.output for step in graph.steps}
    expected_nodes.add(graph.last.output)
    expected_edges = 0
    for step in graph.steps:
        expected_nodes.update(step.inputs)
        expected_edges += len(step.inputs)

    stream = GraphEventStream()
    reporter = GraphEffectReporter(stream, throttle_interval=0.0)

    await reporter.publish_graph(graph, mark_success=True)

    snapshot = reporter.latest_snapshot()
    assert snapshot is not None

    nodes = snapshot["nodes"]
    edges = snapshot["edges"]

    assert len(nodes) == len(expected_nodes)
    assert len(edges) == expected_edges

    html = build_snapshot_html(snapshot, title="Test Snapshot")
    assert "cytoscape" in html
    assert "cytoscape-dagre" in html
    assert "node-image-container" in html
    assert "Metadata" in html
    assert "Test Snapshot" in html
