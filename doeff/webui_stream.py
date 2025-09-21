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
import copy
import heapq
import io
import json
import logging
import re
import queue
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Generator, TypeVar, TYPE_CHECKING

from loguru import logger as loguru_logger

from doeff import Get, Put, do
from doeff.effects import (
    Await,
    Catch,
    Fail,
    Gather,
    Recover,
    Snapshot,
)
from doeff.program import Program
from doeff.types import Effect, EffectFailure
from doeff._vendor import WGraph, WNode, WStep

from PIL import Image as PILImageModule
from PIL.Image import Image as PILImage
import numpy as np

from _webui_snapshot import (
    build_snapshot as _build_snapshot_rust,
    build_snapshot_html as _build_snapshot_html_rust,
)

logger = logging.getLogger(__name__)
loguru_logger = loguru_logger.bind(component="webui_stream")

T = TypeVar("T")


_WEBUI_SERVER_STORE_KEY = "doeff_webui_servers"


@do
def stream_program_to_webui(
    program: Program[T],
    *,
    host: str = "127.0.0.1",
    port: int | None = 8765,
    keep_alive: bool = True,
    graph_push_interval: float = 1.0,
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
        port: Preferred TCP port (``None`` or an occupied port triggers automatic
            selection of a free ephemeral port).
        graph_push_interval: Minimum number of seconds between UI graph updates.
            Use ``0`` or a negative value to disable throttling entirely.

    Returns:
        A new :class:`Program` that forwards all effects but also publishes
        graph updates to the live UI.

    Notes:
        When ``keep_alive`` is ``True`` (the default) the returned program
        pauses after completion until you press ``Ctrl+C``. This keeps the
        web UI visible without additional plumbing.
    """

    server_store: dict[tuple[str, int | None], GraphWebUIServer] | None = yield Get(
        _WEBUI_SERVER_STORE_KEY
    )
    if server_store is None:
        server_store = {}
        yield Put(_WEBUI_SERVER_STORE_KEY, server_store)

    key = (host, port)
    server = server_store.get(key)
    if server is None:
        server = _get_or_create_server(host, port)
        actual_key = (server.host, server.port)
        server_store[actual_key] = server
        if key != actual_key:
            server_store[key] = server
        yield Put(_WEBUI_SERVER_STORE_KEY, server_store)

    host, port = server.host, server.port
    reporter = GraphEffectReporter(
        server.event_stream,
        throttle_interval=graph_push_interval,
    )
    transform = _make_graph_transform(reporter)
    instrumented = program.intercept(transform)
    instrumented = _wrap_with_recover(instrumented, reporter, host, port)
    if keep_alive:
        instrumented = _with_keep_alive(instrumented, host, port)

    result = yield instrumented
    return result


def snapshot_program_to_webui(
    program: Program[T],
    *,
    output_path: str | Path,
    title: str = "doeff Graph Snapshot",
    graph_push_interval: float = 0.0,
) -> Program[T]:
    """Return a Program that saves a standalone HTML graph snapshot upon completion."""

    stream = GraphEventStream()
    reporter = GraphEffectReporter(stream, throttle_interval=graph_push_interval)
    transform = _make_graph_transform(reporter)
    instrumented = program.intercept(transform)
    instrumented = _wrap_with_recover(instrumented, reporter, host=None, port=None)
    instrumented = _with_snapshot_writer(
        instrumented,
        reporter,
        stream,
        Path(output_path).expanduser().resolve(),
        title,
    )
    return instrumented


async def graph_to_webui_html_async(
    graph: WGraph,
    *,
    title: str = "doeff Graph Snapshot",
    mark_success: bool = False,
) -> str:
    """Return an HTML document that renders ``graph`` in the Cytoscape UI."""

    snapshot = await asyncio.to_thread(
        build_graph_snapshot,
        graph,
        mark_success=mark_success,
    )
    return _build_snapshot_html(snapshot, title)


@do
def graph_to_webui_html(
    graph: WGraph,
    *,
    title: str = "doeff Graph Snapshot",
    mark_success: bool = False,
) -> str:
    """Return a Program that yields HTML rendering for ``graph``."""

    html = yield Await(
        graph_to_webui_html_async(
            graph,
            title=title,
            mark_success=mark_success,
        )
    )
    return html


def graph_to_webui_html_blocking(
    graph: WGraph,
    *,
    title: str = "doeff Graph Snapshot",
    mark_success: bool = False,
) -> str:
    """Blocking helper to render ``graph`` to HTML outside of ``@do`` contexts."""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        raise RuntimeError(
            "graph_to_webui_html_blocking() cannot be called while an event loop is running; "
            "use await graph_to_webui_html_async(...) instead."
        )

    return asyncio.run(
        graph_to_webui_html_async(
            graph,
            title=title,
            mark_success=mark_success,
        )
    )


async def write_graph_to_webui_html_async(
    graph: WGraph,
    output_path: str | Path,
    *,
    title: str = "doeff Graph Snapshot",
    mark_success: bool = False,
) -> Path:
    """Async helper to persist ``graph`` as a standalone HTML snapshot."""

    html = await graph_to_webui_html_async(
        graph,
        title=title,
        mark_success=mark_success,
    )
    path = Path(output_path).expanduser().resolve()
    await asyncio.to_thread(_write_snapshot_html_file, path, html)
    loguru_logger.info("Graph snapshot saved to %s", path)
    return path


def write_graph_to_webui_html(
    graph: WGraph,
    output_path: str | Path,
    *,
    title: str = "doeff Graph Snapshot",
    mark_success: bool = False,
) -> Path:
    """Synchronously persist ``graph`` as a standalone HTML snapshot."""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        raise RuntimeError(
            "write_graph_to_webui_html() cannot be called while an event loop is running; "
            "use await write_graph_to_webui_html_async(...) instead."
        )

    return asyncio.run(
        write_graph_to_webui_html_async(
            graph,
            output_path,
            title=title,
            mark_success=mark_success,
        )
    )


# ---------------------------------------------------------------------------
# Graph capture utilities
# ---------------------------------------------------------------------------


def _make_graph_transform(
    reporter: "GraphEffectReporter",
) -> Callable[[Effect], Effect | Program[Effect]]:
    def transform(effect: Effect) -> Effect | Program[Effect]:
        return effect

    return transform


def _wrap_with_recover(
    program: Program[T],
    reporter: "GraphEffectReporter",
    host: str | None,
    port: int | None,
) -> Program[T]:
    """Ensure final snapshots and error reporting using Recover."""

    def failure_fallback(exc: Exception) -> Program[Any]:
        def generator() -> Generator[Any, Any, Any]:
            graph_state = None
            try:
                graph_state = yield Snapshot()
            except Exception:  # pragma: no cover - defensive
                graph_state = None
            if graph_state is not None:
                yield Await(reporter.publish_graph(graph_state))
            yield Await(reporter.publish_error(exc, None))
            destination = "the web UI"
            if host and port:
                destination = f"http://{host}:{port}"
            logger.error(
                "Monitored program finished with failure (%s: %s); inspect %s for details.",
                exc.__class__.__name__,
                exc,
                destination,
            )
            yield Fail(exc)

        return Program(generator)

    def generator() -> Generator[Any, Any, T]:
        result = yield Recover(program, failure_fallback)
        graph_state = None
        try:
            graph_state = yield Snapshot()
        except Exception:  # pragma: no cover - defensive
            graph_state = None
        if graph_state is not None:
            yield Await(reporter.publish_graph(graph_state, mark_success=True))
        destination = "the web UI"
        if host and port:
            destination = f"http://{host}:{port}"
        logger.info(
            "Monitored program completed successfully; inspect %s for the final graph.",
            destination,
        )
        return result

    return Program(generator)


def _with_keep_alive(program: Program[T], host: str, port: int) -> Program[T]:
    """Run a program while keeping the web UI alive until Ctrl+C."""

    def generator() -> Generator[Any, Any, T]:
        results = yield Gather(program, _keep_alive_program(host, port))
        return results[0]

    return Program(generator)


def _with_snapshot_writer(
    program: Program[T],
    reporter: "GraphEffectReporter",
    event_stream: GraphEventStream,
    output_path: Path,
    title: str,
) -> Program[T]:
    """Wrap a program so the final graph snapshot is written to disk."""

    output_path = output_path.expanduser()

    def generator() -> Generator[Any, Any, T]:
        try:
            result = yield program
        except Exception as exc:
            snapshot = reporter.latest_snapshot() or event_stream.last_snapshot()
            html = _build_snapshot_html(snapshot, title)
            yield Await(asyncio.to_thread(_write_snapshot_html_file, output_path, html))
            loguru_logger.info(
                "Graph snapshot saved to %s after failure", output_path
            )
            raise exc

        snapshot = reporter.latest_snapshot() or event_stream.last_snapshot()
        html = _build_snapshot_html(snapshot, title)
        yield Await(asyncio.to_thread(_write_snapshot_html_file, output_path, html))
        loguru_logger.info("Graph snapshot saved to %s", output_path)
        return result

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


def _topologically_sorted_steps(graph: WGraph) -> list[WStep]:
    """Return graph steps in original order - Cytoscape will handle topological sorting."""
    
    # Simply return all steps without manual sorting
    # Cytoscape's dagre layout will handle the topological ordering automatically
    all_steps = list(graph.steps)
    all_steps.append(graph.last)
    
    return all_steps


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

    def publish_snapshot(self, snapshot: dict[str, Any]) -> None:
        event = {
            "type": snapshot.get("type", "snapshot"),
            "nodes": list(snapshot.get("nodes", [])),
            "edges": list(snapshot.get("edges", [])),
        }
        with self._lock:
            self._last_snapshot = copy.deepcopy(snapshot)
            clients = list(self._clients)
        for client in clients:
            client.put(event)

    def last_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._last_snapshot)



@dataclass(frozen=True)
class _NodePayload:
    label: str
    value_repr: str
    meta: dict[str, Any]
    image: str | None
    value_image: str | None
    meta_images: tuple[str, ...]
    meta_id: int | None
    value_id: int
    display_width: float | None
    display_height: float | None


class GraphEffectReporter:
    """Build Cytoscape-ready snapshots from the interpreter graph."""

    IMAGE_PLACEHOLDER = "[image data]"
    _BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")

    def __init__(
        self,
        stream: GraphEventStream,
        *,
        throttle_interval: float | None = None,
    ) -> None:
        # ``throttle_interval`` retained for backward compatibility but ignored.
        self._stream = stream
        self._latest_snapshot: dict[str, Any] | None = None
        self._node_payload_cache: dict[Any, _NodePayload] = {}
        self._last_node_id: str | None = None
        self._error_counter: int = 1

    async def publish_graph(
        self,
        graph,
        *,
        mark_success: bool = False,
        merge: bool = False,
    ) -> None:
        if merge:  # pragma: no cover - legacy compatibility
            loguru_logger.debug("merge flag ignored in simplified reporter")

        snapshot = self._build_snapshot(graph, mark_success=mark_success)
        self._latest_snapshot = copy.deepcopy(snapshot)
        self._last_node_id = snapshot.get("last_node_id")
        self._stream.publish_snapshot(snapshot)

    def build_snapshot(
        self,
        graph,
        *,
        mark_success: bool = False,
    ) -> dict[str, Any]:
        snapshot = self._build_snapshot(graph, mark_success=mark_success)
        self._latest_snapshot = copy.deepcopy(snapshot)
        self._last_node_id = snapshot.get("last_node_id")
        return copy.deepcopy(snapshot)

    def _build_snapshot(
        self,
        graph,
        *,
        mark_success: bool = False,
    ) -> dict[str, Any]:
        steps_override = _topologically_sorted_steps(graph)
        try:
            rust_result = _build_snapshot_rust(
                graph,
                None,
                node_ids=None,
                edge_ids=None,
                node_counter=1,
                edge_counter=1,
                last_node_id=None,
                mark_success=mark_success,
                merge=False,
                payload_getter=self._get_node_payload,
                steps_override=steps_override,
            )
        except Exception as exc:  # pragma: no cover - escalated failure path
            loguru_logger.exception("Rust snapshot builder failed")
            raise

        if not isinstance(rust_result, Mapping):
            raise RuntimeError("Rust snapshot builder returned unexpected payload")

        nodes_list = list(rust_result.get("nodes", []))
        edges_list = list(rust_result.get("edges", []))

        final_node_id: str | None = None
        final_node_candidate = rust_result.get("final_node_id")
        if isinstance(final_node_candidate, str):
            final_node_id = final_node_candidate

        last_node_id: str | None = None
        last_node_candidate = rust_result.get("last_node_id")
        if isinstance(last_node_candidate, str):
            last_node_id = last_node_candidate
        if last_node_id is None:
            last_node_id = final_node_id

        snapshot = {
            "type": "snapshot",
            "nodes": nodes_list,
            "edges": edges_list,
            "final_node_id": final_node_id,
            "last_node_id": last_node_id,
        }
        return snapshot


    async def publish_error(self, error: BaseException, effect: Effect | None) -> None:
        """Publish an error node with stack trace information."""
        loguru_logger.debug(
            "publish_error triggered effect={}",
            effect.__class__.__name__ if effect else None,
        )

        base_snapshot = self._latest_snapshot or self._stream.last_snapshot()
        nodes: list[dict[str, Any]] = copy.deepcopy(base_snapshot.get("nodes", []))
        edges: list[dict[str, Any]] = copy.deepcopy(base_snapshot.get("edges", []))

        node_id = f"error-node-{self._error_counter}"
        edge_id = f"error-edge-{self._error_counter}"
        self._error_counter += 1

        meta = {"message": str(error)}
        if effect is not None:
            meta["effect"] = effect.__class__.__name__

        if isinstance(error, EffectFailure):
            cause = error.cause
            meta["message"] = f"{cause.__class__.__name__}: {cause}"
            if error.creation_context:
                meta["creation"] = error.creation_context.format_full()
            if error.runtime_traceback:
                meta["trace"] = error.runtime_traceback
        else:
            meta["trace"] = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )

        nodes.append(
            {
                "data": {
                    "id": node_id,
                    "label": f"Error: {type(error).__name__}",
                    "value_repr": meta["message"],
                    "meta": meta,
                    "is_error": True,
                }
            }
        )

        if self._last_node_id:
            edges.append(
                {
                    "data": {
                        "id": edge_id,
                        "source": self._last_node_id,
                        "target": node_id,
                    }
                }
            )

        snapshot = {
            "type": "snapshot",
            "nodes": nodes,
            "edges": edges,
            "last_node_id": node_id,
            "final_node_id": base_snapshot.get("final_node_id"),
        }

        self._latest_snapshot = copy.deepcopy(snapshot)
        self._last_node_id = node_id
        self._stream.publish_snapshot(snapshot)

    def _get_node_payload(self, node: Any, step_meta: Any | None) -> _NodePayload:
        loguru_logger.debug(
            "_get_node_payload start node_id={} meta_present={}",
            id(node),
            step_meta is not None,
        )
        cached = self._node_payload_cache.get(node)
        if step_meta is None and cached:
            loguru_logger.debug(
                "node payload cache hit (no meta) node_id={}", id(node)
            )
            return cached

        value_obj = getattr(node, "value", node)
        value_id = id(value_obj)
        meta_id = id(step_meta) if step_meta is not None else None

        if cached and cached.meta_id == meta_id and cached.value_id == value_id:
            loguru_logger.debug(
                "node payload cache hit node_id={} meta_id={}", id(node), meta_id
            )
            return cached

        sanitized_meta = self._sanitize_meta(step_meta or {})
        meta_dict, meta_images = self._redact_meta_images(sanitized_meta)

        image_src: str | None = None
        display_width: float | None = None
        display_height: float | None = None
        if PILImage is not None and isinstance(value_obj, PILImage):
            image_src = self._image_to_data_url(value_obj)
            display_width, display_height = self._infer_display_dimensions(value_obj)
        elif np is not None and self._ndarray_is_image_like(value_obj):
            image_src = self._ndarray_to_data_url(value_obj)
            display_width, display_height = self._infer_display_dimensions(value_obj)

        payload = _NodePayload(
            label=self._value_to_label(value_obj),
            value_repr=self._trimmed_repr(value_obj),
            meta=meta_dict,
            image=image_src,
            value_image=image_src,
            meta_images=tuple(meta_images),
            meta_id=meta_id,
            value_id=value_id,
            display_width=display_width,
            display_height=display_height,
        )
        loguru_logger.debug(
            "node payload cache update node_id={} meta_id={} value_id={}", id(node), meta_id, value_id
        )
        self._node_payload_cache[node] = payload
        return payload

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

    def _extract_image_data_uri(self, blob: Mapping[str, Any]) -> str | None:
        data_field = blob.get("data")
        if not isinstance(data_field, str):
            return None
        mime_field = (
            blob.get("mime_type")
            or blob.get("mime")
            or blob.get("content_type")
        )
        if not isinstance(mime_field, str) or not mime_field.startswith("image/"):
            return None
        trimmed = data_field.strip()
        if not trimmed or len(trimmed) < 64 or len(trimmed) % 4:
            return None
        if not self._BASE64_RE.fullmatch(trimmed):
            return None
        return f"data:{mime_field};base64,{trimmed}"

    def _sanitize_meta(self, value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            value = dict(value)
        sanitized = self._sanitize(value)
        if isinstance(sanitized, dict):
            return sanitized
        return {"value": sanitized}

    def _redact_meta_images(
        self, value: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        images: list[str] = []

        def visit(obj: Any) -> Any:
            if isinstance(obj, str):
                trimmed = obj.strip()
                if trimmed.startswith("data:image/"):
                    if trimmed not in images:
                        images.append(trimmed)
                    return self.IMAGE_PLACEHOLDER
                return obj

            if isinstance(obj, Mapping):
                mapped: dict[str, Any] = {}
                data_uri = self._extract_image_data_uri(obj)
                for key, val in obj.items():
                    key_str = str(key)
                    if (
                        data_uri
                        and key_str.lower() == "data"
                        and isinstance(val, str)
                    ):
                        if data_uri not in images:
                            images.append(data_uri)
                        mapped[key_str] = self.IMAGE_PLACEHOLDER
                        continue
                    mapped[key_str] = visit(val)
                return mapped

            if isinstance(obj, (list, tuple, set)):
                return [visit(item) for item in obj]

            return obj

        redacted = visit(value)
        if isinstance(redacted, dict):
            return redacted, images
        return {"value": redacted}, images

    def latest_snapshot(self) -> dict[str, Any] | None:
        snapshot = self._latest_snapshot
        if snapshot is None:
            return None
        return copy.deepcopy(snapshot)

    def _infer_display_dimensions(self, value: Any) -> tuple[float | None, float | None]:
        width: float | None = None
        height: float | None = None

        if PILImage is not None and isinstance(value, PILImage):
            w, h = value.size
            width, height = float(w), float(h)
        elif np is not None and self._ndarray_is_image_like(value):
            arr = np.asarray(value)
            if arr.ndim == 2:
                height, width = float(arr.shape[0]), float(arr.shape[1])
            elif arr.ndim == 3:
                if arr.shape[0] in (1, 3, 4) and arr.shape[2] not in (1, 3, 4):
                    height, width = float(arr.shape[1]), float(arr.shape[2])
                else:
                    height, width = float(arr.shape[0]), float(arr.shape[1])

        if not width or not height:
            return None, None

        return self._normalize_dimensions(width, height)

    def _normalize_dimensions(self, width: float, height: float) -> tuple[float, float]:
        max_dim = 360.0
        min_dim = 140.0

        longest = max(width, height)
        if longest <= 0:
            return self._default_image_width(), self._default_image_height()

        scale = min(1.0, max_dim / longest)
        width *= scale
        height *= scale

        longest = max(width, height)
        if longest < min_dim:
            scale = min_dim / longest
            width *= scale
            height *= scale
            longest = max(width, height)
            if longest > max_dim:
                scale = max_dim / longest
                width *= scale
                height *= scale

        return round(width, 2), round(height, 2)

    @staticmethod
    def _default_image_width() -> float:
        return 240.0

    @staticmethod
    def _default_image_height() -> float:
        return 180.0

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
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    @staticmethod
    def _ndarray_is_image_like(value: Any) -> bool:
        if np is None:
            return False
        if not isinstance(value, np.ndarray):  # type: ignore[arg-type]
            return False
        if value.size == 0:
            return False
        if value.ndim == 2:
            return True
        if value.ndim == 3 and (
            value.shape[2] in (1, 3, 4) or value.shape[0] in (1, 3, 4)
        ):
            return True
        return False

    def _ndarray_to_data_url(self, array: "np.ndarray") -> str | None:
        if np is None or PILImageModule is None:
            return None
        assert np is not None
        assert PILImageModule is not None
        arr = np.asarray(array)
        if arr.size == 0:
            return None
        arr = np.nan_to_num(arr, nan=0.0, posinf=255.0, neginf=0.0)

        if arr.ndim == 3 and arr.shape[0] in (1, 3, 4) and arr.shape[2] not in (1, 3, 4):
            arr = np.moveaxis(arr, 0, -1)

        if arr.ndim == 3 and arr.shape[2] == 1:
            arr = arr[..., 0]

        if arr.ndim == 3 and arr.shape[2] not in (3, 4):
            return None

        if arr.ndim == 2:
            mode = "L"
        elif arr.ndim == 3 and arr.shape[2] == 3:
            mode = "RGB"
        elif arr.ndim == 3 and arr.shape[2] == 4:
            mode = "RGBA"
        else:
            return None

        if np.issubdtype(arr.dtype, np.bool_):
            arr = arr.astype(np.uint8) * 255
        elif np.issubdtype(arr.dtype, np.integer):
            arr = np.clip(arr, 0, 255).astype(np.uint8)
        else:
            arr = arr.astype(np.float32)
            finite = arr[np.isfinite(arr)]
            if finite.size == 0:
                arr = np.zeros_like(arr, dtype=np.uint8)
            else:
                min_val = float(finite.min())
                max_val = float(finite.max())
                if max_val == min_val:
                    arr = np.zeros_like(arr, dtype=np.uint8)
                else:
                    arr = (arr - min_val) / (max_val - min_val)
                    arr = np.clip(arr, 0.0, 1.0)
                    arr = (arr * 255.0).round().astype(np.uint8)

        arr = np.ascontiguousarray(arr)

        try:
            image = PILImageModule.fromarray(arr, mode)
        except Exception:  # pragma: no cover - fallback when conversion fails
            return None

        return self._image_to_data_url(image)


def build_graph_snapshot(
    graph: WGraph,
    *,
    mark_success: bool = False,
) -> dict[str, Any]:
    """Return a serialisable snapshot for ``graph`` using the Rust accelerator."""

    reporter = GraphEffectReporter(GraphEventStream())
    return reporter.build_snapshot(graph, mark_success=mark_success)


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
        if self._thread and self._thread.is_alive():
            return

        handler = self._build_handler()
        httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._server = httpd
        self.port = httpd.server_port

        print(f"Open http://{self.host}:{self.port} in your browser to view the graph.")
        print("Press Ctrl+C when you're done to shut down the web UI.")

        def serve() -> None:
            with httpd:
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


_ACTIVE_SERVERS: dict[tuple[str, int | None], GraphWebUIServer] = {}
_ACTIVE_SERVERS_LOCK = threading.Lock()


def _get_or_create_server(host: str, port: int | None) -> GraphWebUIServer:
    requested_port = port

    with _ACTIVE_SERVERS_LOCK:
        direct_match = _ACTIVE_SERVERS.get((host, requested_port))
        if direct_match is not None:
            return direct_match

    attempt_port = requested_port if requested_port is not None else 0

    while True:
        server = GraphWebUIServer(host, attempt_port)
        try:
            server.start()
        except OSError:
            if requested_port is not None and attempt_port == requested_port:
                logger.warning(
                    "Port %s is busy; selecting an ephemeral port for graph web UI.",
                    requested_port,
                )
            attempt_port = 0
            continue

        actual_port = server.port
        with _ACTIVE_SERVERS_LOCK:
            _ACTIVE_SERVERS[(host, actual_port)] = server
            if requested_port not in (None, actual_port):
                _ACTIVE_SERVERS[(host, requested_port)] = server
            if requested_port is None:
                _ACTIVE_SERVERS.setdefault((host, None), server)
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
      #graph-container {
        flex: 1 1 auto;
        width: 70vw;
        display: flex;
        flex-direction: column;
        position: relative;
      }
      #controls {
        position: absolute;
        top: 16px;
        left: 16px;
        z-index: 1000;
        display: flex;
        gap: 8px;
        background: rgba(255, 255, 255, 0.95);
        padding: 8px;
        border-radius: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
      }
      #controls button {
        background: #4a90e2;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 8px 16px;
        cursor: pointer;
        font-size: 14px;
        font-weight: 500;
        transition: background 0.2s;
      }
      #controls button:hover {
        background: #357abd;
      }
      #controls button:active {
        transform: translateY(1px);
      }
      #cy {
        flex: 1 1 auto;
        width: 100%;
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
      #details-content {
        flex: 1 1 auto;
        display: flex;
        flex-direction: column;
        gap: 12px;
        overflow: auto;
      }
      #details-json {
        flex: 1 1 auto;
        background: #f0f4f8;
        padding: 12px;
        border-radius: 6px;
        overflow: auto;
        font-family: 'Fira Code', 'SFMono-Regular', ui-monospace, SFMono-Regular, Menlo, Monaco,
          Consolas, 'Liberation Mono', 'Courier New', monospace;
        font-size: 12px;
        color: #1f2937;
      }
      #details-json .json-root {
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      #details-json .json-leaf {
        display: flex;
        gap: 4px;
        align-items: baseline;
        padding-left: 4px;
      }
      #details-json .json-key {
        font-weight: 600;
        color: #0f172a;
      }
      #details-json .json-value {
        word-break: break-word;
      }
      #details-json details.json-node {
        border-left: 2px solid #d9e1ec;
        margin-left: 4px;
        padding-left: 8px;
      }
      #details-json details.json-node > summary {
        cursor: pointer;
        list-style: none;
        font-weight: 600;
        color: #0f172a;
        display: flex;
        align-items: center;
        gap: 6px;
      }
      #details-json details.json-node > summary::-webkit-details-marker {
        display: none;
      }
      #details-json details.json-node > summary::before {
        content: '\\25B6';
        display: inline-block;
        font-size: 10px;
        transform: rotate(0deg);
        transition: transform 0.2s ease;
      }
      #details-json details.json-node[open] > summary::before {
        transform: rotate(90deg);
      }
      #details-json .json-children {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin-top: 6px;
      }
      #details-json .json-empty {
        color: #64748b;
        font-style: italic;
      }
      #details-images {
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      #details-images img {
        max-width: 100%;
        border-radius: 6px;
        border: 1px solid #d9e1ec;
      }
    </style>
    <script src=\"https://unpkg.com/cytoscape@3.26.0/dist/cytoscape.min.js\"></script>
    <script src=\"https://unpkg.com/dagre@0.8.5/dist/dagre.min.js\"></script>
    <script src=\"https://unpkg.com/cytoscape-dagre@2.5.0/cytoscape-dagre.js\"></script>
  </head>
  <body>
    <div id=\"graph-container\">
      <div id=\"controls\">
        <button id=\"btn-fit\" title=\"Fit graph to viewport\">🏠 Home</button>
        <button id=\"btn-zoom-in\" title=\"Zoom in\">➕ Zoom In</button>
        <button id=\"btn-zoom-out\" title=\"Zoom out\">➖ Zoom Out</button>
        <button id=\"btn-layout\" title=\"Re-run layout\">🔄 Re-Layout</button>
      </div>
      <div id=\"cy\"></div>
    </div>
    <div id=\"details\">
      <h2>Node Details</h2>
      <div id=\"details-content\">
        <div id=\"details-json\">Select a node to inspect its metadata.</div>
        <div id=\"details-images\"></div>
      </div>
    </div>
    <script>
      // Initialize layout configuration - disable auto-fit to avoid conflicts
      let layoutConfig = {
        name: 'breadthfirst',
        directed: true,
        padding: 40,
        fit: false,  // Disable auto-fit to avoid viewport conflicts
        spacingFactor: 1.5,
        animate: false
      };
      
      // Check if dagre is available and use it if so
      if (typeof dagre !== 'undefined' && typeof cytoscape !== 'undefined') {
        // Register dagre with cytoscape
        if (typeof cytoscapeDagre !== 'undefined') {
          cytoscape.use(cytoscapeDagre);
          console.log('Dagre extension registered');
          
          // Use dagre layout
          layoutConfig = {
            name: 'dagre',
            rankDir: 'TB',
            nodeSep: 50,
            rankSep: 70,
            edgeSep: 10,
            ranker: 'network-simplex',
            animate: false,
            fit: false,  // Disable auto-fit to avoid viewport conflicts
            padding: 40
          };
          console.log('Using dagre layout');
        }
      } else {
        console.log('Using breadthfirst layout');
      }
      
      const cy = cytoscape({
        container: document.getElementById('cy'),
        elements: [],
        layout: layoutConfig,
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
        .selector('node[is_success]')
        .style({
          'background-color': '#16a34a',
          'border-color': '#166534',
          'color': '#ecfdf5',
          'font-weight': 'bold'
        })
        .selector('node[is_error]')
        .style({
          'background-color': '#dc2626',
          'border-color': '#7f1d1d',
          'color': '#ffffff',
          'font-weight': 'bold'
        })
        .selector('node[image]')
        .style({
          'shape': 'roundrectangle',
          'background-image': 'data(image)',
          'background-fit': 'contain',
          'background-repeat': 'no-repeat',
          'background-position': 'center center',
          'background-opacity': 1,
          'background-color': '#ffffff',
          'border-width': 2,
          'border-color': '#4a90e2',
          'border-style': 'solid',
          'border-radius': 12,
          'width': 'data(image_width)',
          'height': 'data(image_height)',
          'padding': '6px',
          'label': 'data(label)',
          'color': '#222',
          'text-valign': 'bottom',
          'text-margin-y': 10,
          'text-halign': 'center',
          'text-background-color': '#ffffffcc',
          'text-background-opacity': 1,
          'text-background-padding': 4,
          'text-background-shape': 'roundrectangle',
          'font-weight': 'bold'
        })
        .selector('node[image][is_last]')
        .style({
          'border-color': '#ca8a04',
          'border-width': 3,
          'overlay-color': '#facc15',
          'overlay-padding': 4,
          'overlay-opacity': 0.25
        })
        .selector('node[image][is_success]')
        .style({
          'border-color': '#166534',
          'border-width': 3,
          'overlay-color': '#16a34a',
          'overlay-padding': 4,
          'overlay-opacity': 0.2
        })
        .selector('node[image][is_error]')
        .style({
          'border-color': '#7f1d1d',
          'border-width': 3,
          'overlay-color': '#dc2626',
          'overlay-padding': 4,
          'overlay-opacity': 0.3
        })
        .selector('edge')
        .style({
          'curve-style': 'bezier',
          'width': 2,
          'line-color': '#cbd5e1',
          'target-arrow-color': '#cbd5e1',
          'target-arrow-shape': 'triangle'
        });

      const detailsJson = document.getElementById('details-json');
      const detailsImages = document.getElementById('details-images');

      function resetDetails() {
        detailsJson.textContent = 'Select a node to inspect its metadata.';
        detailsImages.innerHTML = '';
      }

      function extractImages(value) {
        const results = new Set();
        const uriByBase = new Map();
        const base64Pattern = /^[A-Za-z0-9+/]+={0,2}$/;

        function visit(current) {
          if (typeof current === 'string') {
            const trimmed = current.trim();
            if (!trimmed) {
              return;
            }
            if (trimmed.startsWith('data:image/')) {
              results.add(trimmed);
              return;
            }
            if (
              trimmed.length >= 64 &&
              trimmed.length % 4 === 0 &&
              base64Pattern.test(trimmed)
            ) {
              const existing = uriByBase.get(trimmed);
              if (existing) {
                results.add(existing);
              } else {
                const fallback = `data:image/png;base64,${trimmed}`;
                uriByBase.set(trimmed, fallback);
                results.add(fallback);
              }
            }
            return;
          }

          if (!current) {
            return;
          }

          if (Array.isArray(current)) {
            current.forEach(visit);
            return;
          }

          if (typeof current === 'object') {
            const inlineImage = current.image;
            if (typeof inlineImage === 'string') {
              const trimmed = inlineImage.trim();
              if (trimmed.startsWith('data:image/')) {
                results.add(trimmed);
              }
            }

            const dataField = typeof current.data === 'string' ? current.data.trim() : null;
            const mimeField =
              typeof current.mime_type === 'string'
                ? current.mime_type
                : typeof current.mime === 'string'
                ? current.mime
                : typeof current.content_type === 'string'
                ? current.content_type
                : null;

            if (
              dataField &&
              dataField.length >= 64 &&
              dataField.length % 4 === 0 &&
              base64Pattern.test(dataField) &&
              mimeField &&
              mimeField.startsWith('image/')
            ) {
              const uri = `data:${mimeField};base64,${dataField}`;
              uriByBase.set(dataField, uri);
              results.add(uri);
            }

            Object.values(current).forEach(visit);
          }
        }

        visit(value);
        return Array.from(results);
      }

      function refreshLayout() {
        // Try dagre first, fallback to breadthfirst
        let layout;
        try {
          layout = cy.layout({ 
            name: 'dagre',
            rankDir: 'TB',
            nodeSep: 50,
            rankSep: 70,
            edgeSep: 10,
            ranker: 'network-simplex',
            animate: false,
            fit: false,  // Disable auto-fit to avoid conflicts
            padding: 40
          });
        } catch (e) {
          console.log('Dagre layout failed, using breadthfirst:', e);
          layout = cy.layout({
            name: 'breadthfirst',
            directed: true,
            padding: 40,
            fit: false,  // Disable auto-fit to avoid conflicts
            spacingFactor: 1.5,
            animate: false
          });
        }
        
        // Run layout and then manually fit when complete
        layout.run();
        
        // Use layoutstop event for reliable timing
        layout.on('layoutstop', function() {
          console.log('Layout complete, fitting viewport');
          cy.fit();
          cy.center();
        });
        
        // Also try after a delay as fallback
        setTimeout(() => {
          cy.fit();
          cy.center();
          console.log('Viewport fitted with', cy.nodes().length, 'nodes');
        }, 150);
      }

      function applySnapshot(nodes, edges) {
        cy.elements().remove();
        
        // Add nodes and edges
        const elements = nodes.concat(edges);
        console.log('Adding elements:', elements.length, 'nodes:', nodes.length, 'edges:', edges.length);
        
        if (elements.length > 0) {
          cy.add(elements);
          console.log('Elements added to cy, total nodes:', cy.nodes().length);
          
          // Use refreshLayout which has fallback logic
          refreshLayout();
        }
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

      function formatPrimitive(value) {
        if (value === null) return 'null';
        if (value === undefined) return 'undefined';
        if (typeof value === 'string') return `"${value}"`;
        if (typeof value === 'number' || typeof value === 'boolean') return String(value);
        return JSON.stringify(value);
      }

      function renderEntry(label, value, collapsed = false) {
        if (value && typeof value === 'object') {
          const details = document.createElement('details');
          details.className = 'json-node';
          if (!collapsed) {
            details.open = true;
          }

          const summary = document.createElement('summary');
          const isArray = Array.isArray(value);
          const suffix = isArray ? `[${value.length}]` : '{…}';
          summary.textContent = label !== null ? `${label} ${suffix}` : suffix;
          details.appendChild(summary);

          const childContainer = document.createElement('div');
          childContainer.className = 'json-children';

          if (isArray) {
            if (!value.length) {
              const empty = document.createElement('div');
              empty.className = 'json-leaf json-empty';
              empty.textContent = '∅';
              childContainer.appendChild(empty);
            } else {
              value.forEach((item, index) => {
                childContainer.appendChild(renderEntry(`[${index}]`, item, true));
              });
            }
          } else {
            const entries = Object.entries(value);
            if (!entries.length) {
              const empty = document.createElement('div');
              empty.className = 'json-leaf json-empty';
              empty.textContent = '∅';
              childContainer.appendChild(empty);
            } else {
              entries.forEach(([childKey, childValue]) => {
                childContainer.appendChild(renderEntry(childKey, childValue, true));
              });
            }
          }

          details.appendChild(childContainer);
          return details;
        }

        const leaf = document.createElement('div');
        leaf.className = 'json-leaf';
        if (label !== null) {
          const keySpan = document.createElement('span');
          keySpan.className = 'json-key';
          keySpan.textContent = `${label}:`;
          leaf.appendChild(keySpan);
        }
        const valueSpan = document.createElement('span');
        valueSpan.className = 'json-value';
        valueSpan.textContent = label !== null ? ` ${formatPrimitive(value)}` : formatPrimitive(value);
        leaf.appendChild(valueSpan);
        return leaf;
      }

      function renderDetailsPanel(payload) {
        detailsJson.innerHTML = '';
        const root = document.createElement('div');
        root.className = 'json-root';
        root.appendChild(renderEntry('id', payload.id, false));
        if ('label' in payload) {
          root.appendChild(renderEntry('label', payload.label, false));
        }
        if ('value' in payload) {
          root.appendChild(renderEntry('value', payload.value, false));
        }
        const metaValue = payload.meta && typeof payload.meta === 'object' ? payload.meta : {};
        root.appendChild(renderEntry('meta', metaValue, true));
        detailsJson.appendChild(root);
      }

      function updateDetails(element) {
        if (!element) {
          resetDetails();
          return;
        }
        const data = element.data();
        const payload = {
          id: data.id,
          label: data.label,
          value: data.value_repr,
          meta: data.meta || {}
        };
        renderDetailsPanel(payload);
        detailsImages.innerHTML = '';

        const images = new Set();
        const addImage = (src) => {
          if (typeof src !== 'string') {
            return;
          }
          const trimmed = src.trim();
          if (trimmed.startsWith('data:image/')) {
            images.add(trimmed);
          }
        };

        addImage(data.image);
        addImage(data.value_image);
        if (Array.isArray(data.meta_images)) {
          data.meta_images.forEach(addImage);
        }

        extractImages(payload.meta).forEach(addImage);

        images.forEach((src) => {
          const img = document.createElement('img');
          img.src = src;
          img.alt = 'Node metadata preview';
          detailsImages.appendChild(img);
        });
      }

      cy.on('tap', (event) => {
        if (event.target === cy) {
          updateDetails(null);
        }
      });

      cy.on('tap', 'node', (event) => {
        updateDetails(event.target);
      });

      // Control button handlers
      document.getElementById('btn-fit').addEventListener('click', () => {
        cy.fit();
        cy.center();
      });

      document.getElementById('btn-zoom-in').addEventListener('click', () => {
        cy.zoom(cy.zoom() * 1.25);
        cy.center();
      });

      document.getElementById('btn-zoom-out').addEventListener('click', () => {
        cy.zoom(cy.zoom() * 0.8);
        cy.center();
      });

      document.getElementById('btn-layout').addEventListener('click', () => {
        refreshLayout();
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


_SSE_SCRIPT_BLOCK = """      const source = new EventSource('events');
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
"""


_SNAPSHOT_SCRIPT_BLOCK = """      const snapshotData = __SNAPSHOT_DATA__;
      try {
        console.log('Loading snapshot with', snapshotData.nodes?.length || 0, 'nodes and', snapshotData.edges?.length || 0, 'edges');
        applySnapshot(snapshotData.nodes || [], snapshotData.edges || []);
        
        // Ensure graph is visible and fitted to viewport
        setTimeout(() => {
          cy.fit();
          cy.center();
          console.log('Graph centered with', cy.nodes().length, 'nodes');
        }, 100);
      } catch (err) {
        console.error('Failed to render graph snapshot', err);
      }
"""


def _build_snapshot_html(snapshot: dict[str, Any] | None, title: str) -> str:
    snapshot_data = snapshot or {"type": "snapshot", "nodes": [], "edges": []}
    return _build_snapshot_html_rust(snapshot_data, title=title)


def _write_snapshot_html_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
