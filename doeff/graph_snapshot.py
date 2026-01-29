"""Graph snapshot visualization using vis.js Network library.

This module provides functions to generate static HTML visualizations of doeff graphs
using vis.js Network, which is simpler and more reliable than Cytoscape.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from loguru import logger

from doeff import Await, do
from doeff.types import WGraph


def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def build_graph_snapshot(
    graph: WGraph,
    *,
    mark_success: bool = False,
) -> dict[str, Any]:
    """Build a vis.js compatible snapshot of a graph.
    
    Args:
        graph: The graph to process
        mark_success: Whether to mark the final node as successful
        
    Returns:
        Dictionary containing nodes and edges for vis.js visualization
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_counter = 1
    node_ids: dict[int, int] = {}  # Map WNode id() to node ID
    node_lookup: dict[int, dict[str, Any]] = {}

    # First pass: create nodes for all steps
    if hasattr(graph, "steps"):
        for step in graph.steps:
            node_id = node_counter
            node_counter += 1

            # Map output node to ID
            if hasattr(step, "output"):
                node_ids[id(step.output)] = node_id

            # WStep has output (WNode) which has value
            value = step.output.value if hasattr(step, "output") and hasattr(step.output, "value") else None

            # Try to get a label from value or meta
            label = str(value) if value is not None else f"Step {node_id}"
            if hasattr(step, "meta") and step.meta and "label" in step.meta:
                label = step.meta["label"]

            # Truncate label if too long
            if len(label) > 30:
                label = label[:27] + "..."

            node_data = {
                "id": node_id,
                "label": label,
                "title": repr(value) if value is not None else "",  # Tooltip
                "color": {
                    "background": "#dbeafe",
                    "border": "#60a5fa"
                }
            }

            nodes.append(node_data)
            node_lookup[node_id] = node_data

    # Process last node (which is also a WStep)
    if hasattr(graph, "last") and graph.last:
        # Check if last node already exists
        if hasattr(graph.last, "output") and id(graph.last.output) in node_ids:
            last_node_id = node_ids[id(graph.last.output)]
            # Mark existing node as last
            for node in nodes:
                if node["id"] == last_node_id:
                    if mark_success:
                        node["color"] = {
                            "background": "#bbf7d0",
                            "border": "#16a34a"
                        }
                    node["font"] = {"bold": True}
                    break
        else:
            # Create new node for last
            node_id = node_counter
            last_node_id = node_id

            # Map output node to ID
            if hasattr(graph.last, "output"):
                node_ids[id(graph.last.output)] = node_id

            value = graph.last.output.value if hasattr(graph.last, "output") and hasattr(graph.last.output, "value") else None

            label = str(value) if value is not None else "Complete"
            if hasattr(graph.last, "meta") and graph.last.meta and "label" in graph.last.meta:
                label = graph.last.meta["label"]

            if len(label) > 30:
                label = label[:27] + "..."

            node_data = {
                "id": node_id,
                "label": label,
                "title": repr(value) if value is not None else "",
                "font": {"bold": True}
            }

            if mark_success:
                node_data["color"] = {
                    "background": "#bbf7d0",
                    "border": "#16a34a"
                }

            nodes.append(node_data)
            node_lookup[node_id] = node_data

    # Second pass: create edges and determine levels
    # Build edges list first
    edges_list: list[dict[str, int]] = []
    seen_edges: set[tuple[int, int]] = set()

    if hasattr(graph, "steps"):
        for step in graph.steps:
            if hasattr(step, "output") and hasattr(step, "inputs"):
                target_id = node_ids.get(id(step.output))
                if target_id:
                    for input_node in step.inputs:
                        source_id = node_ids.get(id(input_node))
                        if source_id:
                            key = (source_id, target_id)
                            if key not in seen_edges:
                                seen_edges.add(key)
                                edges_list.append({
                                    "from": source_id,
                                    "to": target_id
                                })

    # Add edges for last node
    if hasattr(graph, "last") and hasattr(graph.last, "inputs"):
        included_in_steps = hasattr(graph, "steps") and graph.last in graph.steps
        if not included_in_steps:
            target_id = node_ids.get(id(graph.last.output))
            if target_id:
                for input_node in graph.last.inputs:
                    source_id = node_ids.get(id(input_node))
                    if source_id:
                        key = (source_id, target_id)
                        if key not in seen_edges:
                            seen_edges.add(key)
                            edges_list.append({
                                "from": source_id,
                                "to": target_id
                            })

    # Build adjacency for deterministic layout
    adjacency: dict[int, set[int]] = {node["id"]: set() for node in nodes}
    indegree: dict[int, int] = {node["id"]: 0 for node in nodes}

    for edge in edges_list:
        source_id = edge["from"]
        target_id = edge["to"]
        if target_id not in adjacency[source_id]:
            adjacency[source_id].add(target_id)
            indegree[target_id] += 1

    import heapq

    level_map: dict[int, int] = {}
    topo_order: list[int] = []
    heap: list[tuple[int, str, int]] = []

    for node in nodes:
        node_id = node["id"]
        if indegree[node_id] == 0:
            level_map[node_id] = 0
            heapq.heappush(heap, (0, node["label"], node_id))

    while heap:
        level, label, node_id = heapq.heappop(heap)
        topo_order.append(node_id)
        for neighbor in sorted(adjacency[node_id]):
            candidate_level = level_map[node_id] + 1
            if candidate_level > level_map.get(neighbor, 0):
                level_map[neighbor] = candidate_level
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                heapq.heappush(
                    heap,
                    (level_map.get(neighbor, candidate_level), node_lookup[neighbor]["label"], neighbor),
                )

    if len(topo_order) != len(nodes):
        # Fallback to deterministic ordering if cycles are present
        remaining = {node["id"] for node in nodes} - set(topo_order)
        for node_id in remaining:
            topo_order.append(node_id)
            level_map.setdefault(node_id, 0)


    level_buckets: dict[int, list[int]] = defaultdict(list)
    for node_id in topo_order:
        level = level_map.get(node_id, 0)
        level_buckets[level].append(node_id)

    H_SPACING = 220
    V_SPACING = 180

    for level, bucket in level_buckets.items():
        if not bucket:
            continue
        ordered_ids = sorted(bucket, key=lambda node_id: node_lookup[node_id]["label"])
        total_width = (len(ordered_ids) - 1) * H_SPACING
        start_x = -total_width / 2
        for index, node_id in enumerate(ordered_ids):
            node = node_lookup[node_id]
            node["level"] = level
            node["x"] = start_x + index * H_SPACING
            node["y"] = level * V_SPACING
            node["fixed"] = {"x": True, "y": True}
            node["physics"] = False

    # Sort nodes by level then x to ensure deterministic ordering for consumers
    nodes.sort(key=lambda node: (node.get("level", 0), node.get("x", 0)))

    # Add arrows to edges
    for edge in edges_list:
        edge["arrows"] = "to"
        edges.append(edge)

    return {
        "nodes": nodes,
        "edges": edges
    }


def generate_html_template(snapshot_data: dict[str, Any], title: str = "doeff Graph Snapshot") -> str:
    """Generate HTML with vis.js Network visualization.
    
    Args:
        snapshot_data: Graph snapshot dictionary with nodes and edges
        title: Title for the HTML page
        
    Returns:
        Complete HTML string with embedded graph data and controls
    """
    # Escape title for HTML
    title_escaped = escape_html(title)

    # Convert snapshot to JSON
    snapshot_json = json.dumps(snapshot_data, indent=2)

    template = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{title_escaped}</title>
  <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <style>
    body {{
      margin: 0;
      padding: 0;
      font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: #f1f5f9;
    }}
    #header {{
      background: white;
      border-bottom: 1px solid #e2e8f0;
      padding: 12px 20px;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
    }}
    #header h1 {{
      margin: 0;
      font-size: 20px;
      color: #0f172a;
    }}
    #controls {{
      position: absolute;
      top: 70px;
      left: 20px;
      z-index: 1000;
      display: flex;
      gap: 8px;
      padding: 10px;
      background: rgba(255, 255, 255, 0.95);
      border-radius: 8px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
    }}
    #controls button {{
      background: #4a90e2;
      color: white;
      border: none;
      padding: 8px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 14px;
      font-weight: 500;
      transition: background-color 0.2s;
      display: flex;
      align-items: center;
      gap: 4px;
    }}
    #controls button:hover {{
      background: #357abd;
    }}
    #controls button:active {{
      transform: translateY(1px);
    }}
    #network {{
      width: 100vw;
      height: calc(100vh - 57px);
      background: #f8fafc;
    }}
    #status {{
      position: absolute;
      bottom: 20px;
      right: 20px;
      background: white;
      padding: 8px 12px;
      border-radius: 6px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
      font-size: 13px;
      color: #64748b;
    }}
  </style>
</head>
<body>
  <div id="header">
    <h1>{title_escaped}</h1>
  </div>
  
  <div id="controls">
    <button onclick="fitNetwork()" title="Fit graph to viewport">🏠 Home</button>
    <button onclick="zoomIn()" title="Zoom in">➕ Zoom In</button>
    <button onclick="zoomOut()" title="Zoom out">➖ Zoom Out</button>
    <button onclick="redrawLayout()" title="Re-run layout">🔄 Re-Layout</button>
  </div>
  
  <div id="network"></div>
  <div id="status">Ready</div>

  <script type="text/javascript">
    // Graph data from Python
    const graphData = {snapshot_json};
    const nodesArray = graphData.nodes || [];
    const edgesArray = graphData.edges || [];
    const hasManualLayout = nodesArray.every(node => typeof node.x === 'number' && typeof node.y === 'number');
    
    // Create DataSets
    const nodes = new vis.DataSet(nodesArray);
    const edges = new vis.DataSet(edgesArray);
    
    // Get container
    const container = document.getElementById('network');
    
    // Create network data
    const data = {{
      nodes: nodes,
      edges: edges
    }};
    
    // Network options
    const options = hasManualLayout
      ? {{
          layout: {{
            improvedLayout: false
          }},
          physics: {{
            enabled: false
          }},
          interaction: {{
            hover: true,
            tooltipDelay: 200,
            zoomView: true,
            dragView: true
          }}
        }}
      : {{
          layout: {{
            hierarchical: {{
              enabled: true,
              direction: "UD",  // Up-Down for DAG
              sortMethod: "directed",
              shakeTowards: "roots",
              levelSeparation: 120,
              nodeSpacing: 180,
              treeSpacing: 220,
              blockShifting: true,
              edgeMinimization: true
            }}
          }},
          physics: {{
            enabled: false
          }},
          interaction: {{
            hover: true,
            tooltipDelay: 200,
            zoomView: true,
            dragView: true
          }}
        }};

    const optionsEdges = {{
      edges: {{
        smooth: {{
          enabled: true,
          type: 'cubicBezier',
          forceDirection: 'vertical',
          roundness: 0.4
        }},
        arrows: {{
          to: {{
            enabled: true,
            scaleFactor: 1
          }}
        }},
        color: {{
          color: '#94a3b8',
          hover: '#64748b'
        }},
        width: 2
      }},
      nodes: {{
        shape: 'box',
        margin: 12,
        widthConstraint: {{
          minimum: 100,
          maximum: 200
        }},
        font: {{
          size: 14,
          face: 'Inter, Segoe UI, system-ui, sans-serif'
        }},
        borderWidth: 2,
        shadow: {{
          enabled: true,
          color: 'rgba(0, 0, 0, 0.1)',
          size: 4,
          x: 0,
          y: 2
        }}
      }},
    }};

    const mergedOptions = Object.assign({{}}, options, optionsEdges);

    // Create network
    let network = new vis.Network(container, data, mergedOptions);
    
    // Status element
    const statusEl = document.getElementById('status');
    
    // Event handlers
    network.on("click", function(params) {{
      if (params.nodes.length > 0) {{
        const nodeId = params.nodes[0];
        const node = nodes.get(nodeId);
        statusEl.textContent = `Selected: ${{node.label}}`;
      }}
    }});
    
    network.on("stabilized", function() {{
      statusEl.textContent = `Graph ready: ${{nodes.length}} nodes, ${{edges.length}} edges`;
    }});
    
    // Control functions
    function fitNetwork() {{
      network.fit({{
        animation: {{
          duration: 500,
          easingFunction: 'easeInOutQuad'
        }}
      }});
      statusEl.textContent = 'Fitted to viewport';
    }}
    
    function zoomIn() {{
      const scale = network.getScale();
      network.moveTo({{
        scale: scale * 1.2,
        animation: {{
          duration: 200,
          easingFunction: 'easeInOutQuad'
        }}
      }});
      statusEl.textContent = 'Zoomed in';
    }}
    
    function zoomOut() {{
      const scale = network.getScale();
      network.moveTo({{
        scale: scale * 0.8,
        animation: {{
          duration: 200,
          easingFunction: 'easeInOutQuad'
        }}
      }});
      statusEl.textContent = 'Zoomed out';
    }}
    
    function redrawLayout() {{
      // Re-apply hierarchical layout
      network.setOptions(options);
      network.redraw();
      setTimeout(() => {{
        network.fit();
        statusEl.textContent = 'Layout refreshed';
      }}, 100);
    }}
    
    // Initial fit
    network.once('afterDrawing', function() {{
      network.fit();
      statusEl.textContent = `Loaded: ${{nodes.length}} nodes, ${{edges.length}} edges`;
    }});
    
    // Log success
    console.log('vis.js Network created successfully');
    console.log('Nodes:', nodes.length);
    console.log('Edges:', edges.length);
  </script>
</body>
</html>"""

    return template


# Keep the same async/Program wrapper functions as the original
async def graph_to_html_async(
    graph: WGraph,
    *,
    title: str = "doeff Graph Snapshot",
    mark_success: bool = False,
) -> str:
    """Generate an HTML visualization of a graph using vis.js asynchronously.
    
    Args:
        graph: The graph to visualize
        title: Title for the HTML page
        mark_success: Whether to mark the final node as successful
        
    Returns:
        HTML string containing the complete visualization
    """
    # Build the snapshot data structure in a thread to avoid blocking
    snapshot = await asyncio.to_thread(
        build_graph_snapshot,
        graph,
        mark_success=mark_success,
    )

    # Generate HTML from the snapshot
    html = generate_html_template(snapshot, title=title)
    return html


@do
def graph_to_html(
    graph: WGraph,
    *,
    title: str = "doeff Graph Snapshot",
    mark_success: bool = False,
) -> str:
    """Generate an HTML visualization of a graph using vis.js (Program version).
    
    Args:
        graph: The graph to visualize
        title: Title for the HTML page
        mark_success: Whether to mark the final node as successful
        
    Returns:
        Program that yields HTML string containing the complete visualization
    """
    html = yield Await(
        graph_to_html_async(
            graph,
            title=title,
            mark_success=mark_success,
        )
    )
    return html


async def write_graph_html_async(
    graph: WGraph,
    output_path: str | Path,
    *,
    title: str = "doeff Graph Snapshot",
    mark_success: bool = False,
) -> Path:
    """Write a graph visualization to an HTML file using vis.js asynchronously.
    
    Args:
        graph: The graph to visualize
        output_path: Path where to save the HTML file
        title: Title for the HTML page
        mark_success: Whether to mark the final node as successful
        
    Returns:
        Path to the written file
    """
    html = await graph_to_html_async(
        graph,
        title=title,
        mark_success=mark_success,
    )

    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write file in thread to avoid blocking
    await asyncio.to_thread(path.write_text, html, encoding="utf-8")

    logger.info("Graph snapshot (vis.js) saved to {}", path)
    return path


@do
def write_graph_html(
    graph: WGraph,
    output_path: str | Path,
    *,
    title: str = "doeff Graph Snapshot",
    mark_success: bool = False,
) -> Path:
    """Write a graph visualization to an HTML file (Program version).
    
    Args:
        graph: The graph to visualize
        output_path: Path where to save the HTML file
        title: Title for the HTML page
        mark_success: Whether to mark the final node as successful
        
    Returns:
        Program that yields the path to the written file
    """
    path = yield Await(
        write_graph_html_async(
            graph,
            output_path,
            title=title,
            mark_success=mark_success,
        )
    )
    return path
