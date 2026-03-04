from doeff.graph_snapshot import build_graph_snapshot
from doeff.types import WGraph, WNode, WStep


def _labels_by_id(snapshot: dict[str, object]) -> dict[int, str]:
    nodes = snapshot["nodes"]
    assert isinstance(nodes, list)
    mapping: dict[int, str] = {}
    for node in nodes:
        assert isinstance(node, dict)
        node_id = node.get("id")
        label = node.get("label")
        assert isinstance(node_id, int)
        assert isinstance(label, str)
        mapping[node_id] = label
    return mapping


def test_build_graph_snapshot_uses_step_meta_label_and_edges() -> None:
    source_node = WNode("source")
    sink_node = WNode("sink")
    source_step = WStep(inputs=(), output=source_node)
    sink_step = WStep(inputs=(source_node,), output=sink_node, meta={"label": "Labeled sink"})
    graph = WGraph(last=sink_step, steps=frozenset({source_step, sink_step}))

    snapshot = build_graph_snapshot(graph)
    labels = _labels_by_id(snapshot)
    edges = snapshot["edges"]

    assert set(labels.values()) == {"source", "Labeled sink"}
    assert isinstance(edges, list)
    assert len(edges) == 1
    edge = edges[0]
    assert isinstance(edge, dict)
    source_id = edge.get("from")
    sink_id = edge.get("to")
    assert isinstance(source_id, int)
    assert isinstance(sink_id, int)
    assert labels[source_id] == "source"
    assert labels[sink_id] == "Labeled sink"


def test_build_graph_snapshot_adds_last_step_when_missing_from_steps() -> None:
    source_node = WNode("source")
    source_step = WStep(inputs=(), output=source_node)
    final_step = WStep(inputs=(source_node,), output=WNode("final"))
    graph = WGraph(last=final_step, steps=frozenset({source_step}))

    snapshot = build_graph_snapshot(graph, mark_success=True)
    labels = _labels_by_id(snapshot)
    edges = snapshot["edges"]

    assert isinstance(edges, list)
    assert len(labels) == 2
    assert len(edges) == 1

    final_id = next(node_id for node_id, label in labels.items() if label == "final")
    final_node = next(
        node
        for node in snapshot["nodes"]
        if isinstance(node, dict) and node.get("id") == final_id
    )
    assert isinstance(final_node, dict)
    assert final_node.get("font") == {"bold": True}
    assert final_node.get("color") == {"background": "#bbf7d0", "border": "#16a34a"}
