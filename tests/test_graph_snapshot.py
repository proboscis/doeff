from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from doeff._vendor import WGraph, WNode, WStep
from doeff.graph_snapshot import (
    build_graph_snapshot,
    graph_to_html_async,
    write_graph_html_async,
)


def _sample_graph() -> WGraph:
    roots = [WNode(f"input_{idx}") for idx in range(2)]
    steps = [WStep((), node) for node in roots]

    middle = WNode("middle")
    middle_step = WStep((roots[0], roots[1]), middle, meta={"label": "Combine"})
    steps.append(middle_step)

    output = WNode("result")
    last_step = WStep((middle,), output, meta={"label": "Result"})

    return WGraph(last=last_step, steps=frozenset(steps + [last_step]))


def test_build_graph_snapshot_includes_nodes_and_edges() -> None:
    graph = _sample_graph()

    snapshot = build_graph_snapshot(graph, mark_success=True)

    assert set(snapshot.keys()) == {"nodes", "edges"}
    assert len(snapshot["nodes"]) == 4  # two roots + middle + result
    assert len(snapshot["edges"]) == 3  # edges to middle (2) and result (1)

    # Ensure nodes share deterministic ordering and coordinates per level
    levels: dict[int, list[float]] = {}
    for node in snapshot["nodes"]:
        assert "x" in node and "y" in node
        levels.setdefault(node["level"], []).append(node["x"])
    for positions in levels.values():
        assert positions == sorted(positions)

    result_node = next(node for node in snapshot["nodes"] if node["label"] == "Result")
    assert result_node.get("color", {}).get("background") == "#bbf7d0"
    assert result_node.get("color", {}).get("border") == "#16a34a"


@pytest.mark.asyncio
async def test_graph_to_html_async_produces_visjs_markup() -> None:
    html = await graph_to_html_async(_sample_graph(), title="Snapshot Demo")

    assert "vis-network" in html
    assert "Snapshot Demo" in html
    assert "const graphData" in html


@pytest.mark.asyncio
async def test_write_graph_html_async_writes_file(tmp_path: Path) -> None:
    target = tmp_path / "graph.html"
    written = await write_graph_html_async(_sample_graph(), target, title="Snapshot")

    assert written.exists()
    contents = written.read_text(encoding="utf-8")
    assert "Snapshot" in contents
    assert "vis-network" in contents
