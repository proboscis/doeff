"""Utilities for streaming graph effects to a lightweight Cytoscape web UI.

This module provides a helper that wraps a :class:`~doeff.program.Program`
and mirrors the graph related effects (``graph.step`` / ``graph.annotate``)
to a small single-file web UI.  The UI is powered by Cytoscape.js served
from a background HTTP server started on demand.  Updates are sent by
server-sent events so that the browser can reflect the evolving structure
in real time.

Usage example
-------------

.. code-block:: python

    from doeff import ProgramInterpreter, Step
    from doeff.webui_stream import stream_program_to_webui

    @do
    def workflow():
        yield Step("Load input")
        yield Step("Run model")
        yield Step(Image.open("preview.png"))

    interpreter = ProgramInterpreter()
    instrumented = stream_program_to_webui(workflow())
    asyncio.run(interpreter.run(instrumented))

After invoking ``stream_program_to_webui`` open the reported URL in a
browser (``http://127.0.0.1:8765`` by default) to watch the graph evolve.
If a step value is a PIL image, the UI renders it directly inside the node.
"""

from __future__ import annotations

import asyncio
import base64
import io
import itertools
import json
import logging
import queue
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections.abc import Mapping
from typing import Any, Callable, Dict, Generator, TypeVar

from doeff.effects import Await, Catch, Gather, Snapshot
from doeff.program import Program
from doeff.types import Effect

try:  # Optional Pillow dependency
    from PIL.Image import Image as PILImage
except Exception:  # pragma: no cover - Pillow may be absent in some envs
    PILImage = None  # type: ignore

logger = logging.getLogger(__name__)

T = TypeVar("T")


def stream_program_to_webui(
    program: Program[T],
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    keep_alive: bool = True,
) -> Program[T]:
    """Return a Program that streams graph effects to a Cytoscape web UI.

    The returned program behaves identically to the original program but any
    ``graph.step`` and ``graph.annotate`` effects are mirrored to the bundled
    web UI.  A background HTTP server (serving both the static page and the
    server-sent event feed) is started on the first invocation per
    ``host``/``port`` combination.

    Args:
        program: Original program to execute.
        host: Host name for the web UI server.
        port: TCP port for the web UI server.

    Returns:
        A new :class:`Program` that forwards all effects but also publishes
        graph updates to the live UI.

    Notes:
        When ``keep_alive`` is ``True`` (the default) the returned program
        pauses after completion until you press ``Ctrl+C``. This keeps the
        web UI visible without additional plumbing.
    """

    server = _get_or_create_server(host, port)
    reporter = GraphEffectReporter(server.event_stream)
    transform = _make_graph_transform(reporter)
    instrumented = program.intercept(transform)
    if not keep_alive:
        return instrumented
    return _with_keep_alive(instrumented, host, port)


# ---------------------------------------------------------------------------
# Graph capture utilities
# ---------------------------------------------------------------------------


def _make_graph_transform(
    reporter: "GraphEffectReporter",
) -> Callable[[Effect], Effect | Program[Effect]]:
    tracked_tags = {
        "graph.step",
        "graph.annotate",
        "gather.gather",
        "gather.gather_dict",
    }

    def transform(effect: Effect) -> Effect | Program[Effect]:
        if effect.tag not in tracked_tags:
            return effect

        def wrapper() -> Generator[Any, Any, Effect]:
            result = yield effect
            graph_state = yield Snapshot()
            reporter.publish_graph(graph_state)
            return result

        return Program(wrapper)

    return transform


def _with_keep_alive(program: Program[T], host: str, port: int) -> Program[T]:
    """Run a program while keeping the web UI alive until Ctrl+C."""

    def generator() -> Generator[Any, Any, T]:
        results = yield Gather(program, _keep_alive_program(host, port))
        return results[0]

    return Program(generator)


def _sleep_once_program(delay: float = 0.25) -> Program[bool]:
    """Return a Program that sleeps for ``delay`` seconds using Await effect."""

    def generator() -> Generator[Any, Any, bool]:
        yield Await(asyncio.sleep(delay))
        return True

    return Program(generator)


def _handle_keep_alive_exception(exc: BaseException) -> bool:
    """Handle exceptions during keep-alive wait loop."""

    if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
        return False
    raise exc


def _keep_alive_program(host: str | None, port: int | None) -> Program[None]:
    """Return a Program that blocks until Ctrl+C is pressed."""

    def generator() -> Generator[Any, Any, None]:
        if host and port:
            logger.info(
                "Web UI streaming active at http://%s:%s – press Ctrl+C to stop",
                host,
                port,
            )
        else:
            logger.info("Web UI streaming active – press Ctrl+C to stop")

        keep_running = True
        while keep_running:
            keep_running = yield Catch(
                _sleep_once_program(5),
                _handle_keep_alive_exception,
            )

        return None

    return Program(generator)


# ---------------------------------------------------------------------------
# Graph state tracking and event publication
# ---------------------------------------------------------------------------


class GraphEventStream:
    """Thread-safe fan-out of graph snapshots to SSE clients."""

    def __init__(self) -> None:
        self._clients: set[queue.Queue[dict[str, Any]]] = set()
        self._lock = threading.Lock()
        self._last_snapshot: dict[str, Any] = {"type": "snapshot", "nodes": [], "edges": []}

    def register(self) -> queue.Queue[dict[str, Any]]:
        client: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            self._clients.add(client)
            client.put(self._last_snapshot)
        return client

    def unregister(self, client: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._clients.discard(client)

    def publish_snapshot(
        self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
    ) -> None:
        event = {"type": "snapshot", "nodes": nodes, "edges": edges}
        with self._lock:
            self._last_snapshot = event
            clients = list(self._clients)
        for client in clients:
            client.put(event)


class GraphEffectReporter:
    """Build Cytoscape-ready snapshots from the interpreter graph."""

    def __init__(self, stream: GraphEventStream) -> None:
        self._stream = stream
        self._node_ids: dict[Any, str] = {}
        self._node_counter = itertools.count(1)

    def publish_graph(self, graph) -> None:
        nodes_payload: dict[str, dict[str, Any]] = {}
        edges_payload: list[dict[str, Any]] = []
        seen_nodes: set[Any] = set()
        final_node_id: str | None = None

        all_steps = set(graph.steps)
        all_steps.add(graph.last)

        def ensure_node(
            node: Any,
            label: str,
            value_repr: str,
            meta: dict[str, Any],
            image: str | None = None,
        ) -> str:
            node_id = self._node_ids.get(node)
            if node_id is None:
                node_id = f"node-{next(self._node_counter)}"
                self._node_ids[node] = node_id
            if node_id not in nodes_payload:
                data = {
                    "id": node_id,
                    "label": label,
                    "value_repr": value_repr,
                    "meta": meta,
                }
                if image is not None:
                    data["image"] = image
                nodes_payload[node_id] = {"data": data}
            else:
                existing = nodes_payload[node_id]["data"]
                existing.setdefault("meta", {}).update(meta)
                if image is not None and "image" not in existing:
                    existing["image"] = image
            return node_id

        for step in all_steps:
            output_node = step.output
            seen_nodes.add(output_node)
            label = self._value_to_label(output_node.value)
            meta_dict = self._sanitize_meta(step.meta)
            value_repr = self._trimmed_repr(output_node.value)
            image_src = None
            if PILImage is not None and isinstance(output_node.value, PILImage):
                image_src = self._image_to_data_url(output_node.value)

            target_id = ensure_node(output_node, label, value_repr, meta_dict, image_src)
            if step is graph.last:
                final_node_id = target_id

            for idx, input_node in enumerate(step.inputs):
                seen_nodes.add(input_node)
                source_id = ensure_node(
                    input_node,
                    self._value_to_label(input_node.value),
                    self._trimmed_repr(input_node.value),
                    {},
                )
                edge_id = f"edge-{len(edges_payload) + 1}"
                edges_payload.append(
                    {"data": {"id": edge_id, "source": source_id, "target": target_id}}
                )

        # Remove node ids that are no longer present
        for node in list(self._node_ids):
            if node not in seen_nodes:
                del self._node_ids[node]

        if final_node_id is None and graph.last.output in self._node_ids:
            final_node_id = self._node_ids[graph.last.output]

        if final_node_id is not None and final_node_id in nodes_payload:
            nodes_payload[final_node_id]["data"]["is_last"] = True

        self._stream.publish_snapshot(
            list(nodes_payload.values()),
            edges_payload,
        )

    @staticmethod
    def _value_to_label(value: Any) -> str:
        if value is None:
            return "None"
        if isinstance(value, str):
            return value if len(value) <= 80 else f"{value[:77]}..."
        if isinstance(value, (int, float, bool)):
            return str(value)
        if PILImage is not None and isinstance(value, PILImage):
            return "Image"
        return type(value).__name__

    @staticmethod
    def _image_to_data_url(image: PILImage) -> str:
        buffer = io.BytesIO()
        # Always export PNG to ensure wide browser support.
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def _sanitize(self, value: Any) -> Any:
        """Return JSON-serialisable data for metadata/value display."""
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, Mapping):
            return {str(k): self._sanitize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._sanitize(v) for v in value]
        if PILImage is not None and isinstance(value, PILImage):
            return {"image": self._image_to_data_url(value)}
        try:
            json.dumps(value)
        except Exception:
            return self._trimmed_repr(value)
        return value

    @staticmethod
    def _trimmed_repr(value: Any, limit: int = 160) -> str:
        text = repr(value)
        if len(text) <= limit:
            return text
        return f"{text[:limit - 3]}..."

    def _sanitize_meta(self, value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            value = dict(value)
        sanitized = self._sanitize(value)
        if isinstance(sanitized, dict):
            return sanitized
        return {"value": sanitized}

# ---------------------------------------------------------------------------
# HTTP server serving the Cytoscape UI and the SSE feed
# ---------------------------------------------------------------------------


class _GraphRequestHandler(BaseHTTPRequestHandler):
    """Serve the static UI and stream events via server-sent events."""

    event_stream: GraphEventStream

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path in {"/", "/index.html"}:
            self._serve_index()
            return
        if self.path == "/events":
            self._serve_events()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003 - match base class
        # Silence default stdout logging; developers can add loguru/structlog if needed.
        return

    def _serve_index(self) -> None:
        body = _INDEX_HTML.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        client_queue = self.event_stream.register()
        try:
            while True:
                event = client_queue.get()
                payload = json.dumps(event)
                message = f"data: {payload}\n\n".encode("utf-8")
                self.wfile.write(message)
                self.wfile.flush()
        except Exception:
            # The client disconnected; just fall through to unregister.
            pass
        finally:
            self.event_stream.unregister(client_queue)


class GraphWebUIServer:
    """Start and manage the background HTTP server for the web UI."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.event_stream = GraphEventStream()
        self._thread: threading.Thread | None = None
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        print("Open http://127.0.0.1:8765 in your browser to view the graph.")
        print("Press Ctrl+C when you're done to shut down the web UI.")

        if self._thread and self._thread.is_alive():
            return

        def serve() -> None:
            handler = self._build_handler()
            with ThreadingHTTPServer((self.host, self.port), handler) as httpd:
                self._server = httpd
                httpd.serve_forever()

        self._thread = threading.Thread(target=serve, daemon=True)
        self._thread.start()
        logger.info(
            "Graph Web UI available at http://%s:%s", self.host, self.port
        )

    def _build_handler(self):  # type: ignore[override]
        stream = self.event_stream

        class Handler(_GraphRequestHandler):
            event_stream = stream

        return Handler


_ACTIVE_SERVERS: dict[tuple[str, int], GraphWebUIServer] = {}
_ACTIVE_SERVERS_LOCK = threading.Lock()


def _get_or_create_server(host: str, port: int) -> GraphWebUIServer:
    key = (host, port)
    with _ACTIVE_SERVERS_LOCK:
        server = _ACTIVE_SERVERS.get(key)
        if server is None:
            server = GraphWebUIServer(host, port)
            server.start()
            _ACTIVE_SERVERS[key] = server
    return server


_INDEX_HTML = """<!DOCTYPE html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <title>doeff Graph Stream</title>
    <style>
      body {
        margin: 0;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        display: flex;
        height: 100vh;
        overflow: hidden;
      }
      #cy {
        flex: 1 1 auto;
        width: 70vw;
        height: 100vh;
        background: #f5f7fa;
      }
      #details {
        width: 30vw;
        max-width: 420px;
        padding: 16px;
        border-left: 1px solid #d9e1ec;
        background: #ffffff;
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
      }
      #details h2 {
        margin-top: 0;
      }
      #details pre {
        flex: 1 1 auto;
        background: #f0f4f8;
        padding: 12px;
        overflow: auto;
        border-radius: 6px;
        white-space: pre-wrap;
        word-break: break-word;
      }
    </style>
    <script src=\"https://unpkg.com/cytoscape@3.26.0/dist/cytoscape.min.js\"></script>
  </head>
  <body>
    <div id=\"cy\"></div>
    <div id=\"details\">
      <h2>Node Details</h2>
      <pre id=\"details-content\">Select a node to inspect its metadata.</pre>
    </div>
    <script>
      const cy = cytoscape({
        container: document.getElementById('cy'),
        elements: [],
        layout: { name: 'breadthfirst', directed: true, padding: 40 },
        wheelSensitivity: 0.2
      });

      cy.style()
        .selector('node')
        .style({
          'shape': 'roundrectangle',
          'width': 'label',
          'height': 'label',
          'padding': '12px',
          'background-color': '#4a90e2',
          'border-width': 2,
          'border-color': '#2662a6',
          'label': 'data(label)',
          'color': '#ffffff',
          'text-wrap': 'wrap',
          'text-max-width': 160,
          'text-valign': 'center',
          'font-size': 12
        })
        .selector('node[is_last]')
        .style({
          'background-color': '#facc15',
          'border-color': '#ca8a04',
          'color': '#1f2937'
        })
        .selector('node[image]')
        .style({
          'background-image': 'data(image)',
          'background-fit': 'cover cover',
          'background-opacity': 1,
          'border-width': 2,
          'border-color': '#4a90e2',
          'label': 'data(label)',
          'color': '#222',
          'text-valign': 'bottom',
          'text-margin-y': -6,
          'font-weight': 'bold'
        })
        .selector('edge')
        .style({
          'curve-style': 'bezier',
          'width': 2,
          'line-color': '#cbd5e1',
          'target-arrow-color': '#cbd5e1',
          'target-arrow-shape': 'triangle'
        });

      const detailsContent = document.getElementById('details-content');

      function refreshLayout() {
        cy.layout({ name: 'breadthfirst', directed: true, padding: 40 }).run();
      }

      function applySnapshot(nodes, edges) {
        cy.elements().remove();
        cy.add(nodes.concat(edges));
        refreshLayout();
      }

      function applyAdd(nodes, edges) {
        if (nodes && nodes.length) {
          cy.add(nodes);
        }
        if (edges && edges.length) {
          cy.add(edges);
        }
        refreshLayout();
      }

      function updateMeta(nodeId, meta) {
        const node = cy.getElementById(nodeId);
        if (!node) return;
        const current = node.data('meta') || {};
        node.data('meta', Object.assign({}, current, meta));
      }

      function updateDetails(element) {
        if (!element) {
          detailsContent.textContent = 'Select a node to inspect its metadata.';
          return;
        }
        const data = element.data();
        const payload = {
          id: data.id,
          label: data.label,
          value: data.value_repr,
          meta: data.meta || {}
        };
        detailsContent.textContent = JSON.stringify(payload, null, 2);
      }

      cy.on('tap', (event) => {
        if (event.target === cy) {
          updateDetails(null);
        }
      });

      cy.on('tap', 'node', (event) => {
        updateDetails(event.target);
      });

      const source = new EventSource('events');
      source.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === 'snapshot') {
            applySnapshot(payload.nodes || [], payload.edges || []);
          } else if (payload.type === 'add') {
            applyAdd(payload.nodes || [], payload.edges || []);
          } else if (payload.type === 'update_meta') {
            updateMeta(payload.node_id, payload.meta || {});
          }
        } catch (err) {
          console.error('Failed to process graph event', err);
        }
      };
    </script>
  </body>
</html>
"""
