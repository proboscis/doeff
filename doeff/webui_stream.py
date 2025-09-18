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
import re
import queue
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections.abc import Mapping
from typing import Any, Callable, Dict, Generator, TypeVar, TYPE_CHECKING

from doeff.effects import (
    Await,
    Catch,
    Fail,
    Gather,
    GatherDict,
    GatherDictEffect,
    GatherEffect,
    GraphAnnotateEffect,
    GraphStepEffect,
    Recover,
    Snapshot,
)
from doeff.program import Program
from doeff.types import Effect, EffectFailure

try:  # Optional Pillow dependency
    from PIL import Image as PILImageModule
    from PIL.Image import Image as PILImage
except Exception:  # pragma: no cover - Pillow may be absent in some envs
    PILImageModule = None  # type: ignore
    PILImage = None  # type: ignore

try:  # Optional NumPy dependency
    import numpy as np
except Exception:  # pragma: no cover - NumPy may be absent in some envs
    np = None  # type: ignore

if TYPE_CHECKING:  # pragma: no cover - only for typing
    import numpy as np

logger = logging.getLogger(__name__)

T = TypeVar("T")


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

    server = _get_or_create_server(host, port)
    host, port = server.host, server.port
    reporter = GraphEffectReporter(
        server.event_stream,
        throttle_interval=graph_push_interval,
    )
    transform = _make_graph_transform(reporter)
    instrumented = program.intercept(transform)
    instrumented = _wrap_with_recover(instrumented, reporter, host, port)
    if not keep_alive:
        return instrumented
    return _with_keep_alive(instrumented, host, port)


# ---------------------------------------------------------------------------
# Graph capture utilities
# ---------------------------------------------------------------------------


def _make_graph_transform(
    reporter: "GraphEffectReporter",
) -> Callable[[Effect], Effect | Program[Effect]]:
    tracked_types = (
        GraphStepEffect,
        GraphAnnotateEffect,
        GatherEffect,
        GatherDictEffect,
    )

    gather_effect_types = (GatherEffect, GatherDictEffect)

    processed_effect_ids: set[int] = set()
    gather_depth = 0

    def transform(effect: Effect) -> Effect | Program[Effect]:
        nonlocal gather_depth
        if not isinstance(effect, tracked_types):
            return effect

        effect_id = id(effect)
        if effect_id in processed_effect_ids:
            return effect

        def wrapper() -> Generator[Any, Any, Effect]:
            nonlocal gather_depth
            is_gather_effect = isinstance(effect, gather_effect_types)
            try:
                processed_effect_ids.add(effect_id)
                if is_gather_effect:
                    gather_depth += 1
                result = yield effect
            except Exception as exc:  # pragma: no cover - passthrough
                reporter.publish_error(exc, effect)
                raise
            finally:
                processed_effect_ids.discard(effect_id)
                if is_gather_effect and gather_depth > 0:
                    gather_depth -= 1
            try:
                graph_state = yield Snapshot()
            except Exception as exc:  # pragma: no cover - passthrough
                reporter.publish_error(exc, None)
                raise
            reporter.publish_graph(graph_state, merge=gather_depth > 0)
            return result

        return Program(wrapper)

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
                reporter.publish_graph(graph_state)
            reporter.publish_error(exc, None)
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
            reporter.publish_graph(graph_state, mark_success=True)
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

    def last_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._last_snapshot


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
        self._stream = stream
        self._node_ids: dict[Any, str] = {}
        self._node_counter = itertools.count(1)
        self._last_node_id: str | None = None
        self._edge_ids: dict[tuple[Any, Any], str] = {}
        self._edge_counter = itertools.count(1)
        self._throttle_interval = (
            float(throttle_interval)
            if throttle_interval is not None and throttle_interval > 0
            else 0.0
        )
        self._lock = threading.Lock()
        self._last_publish_time = 0.0
        self._pending_snapshot: tuple[
            list[dict[str, Any]],
            list[dict[str, Any]],
            bool,
        ] | None = None
        self._pending_timer: threading.Timer | None = None
        self._latest_snapshot: dict[str, Any] | None = None

    def publish_graph(
        self,
        graph,
        *,
        mark_success: bool = False,
        merge: bool = False,
    ) -> None:
        baseline_snapshot = self._latest_snapshot if merge else None
        if merge and baseline_snapshot is None:
            baseline_snapshot = self._stream.last_snapshot()

        nodes_payload: dict[str, dict[str, Any]] = {}
        edges_payload: dict[str, dict[str, Any]] = {}

        if merge and baseline_snapshot:
            for node in baseline_snapshot.get("nodes", []):
                data = dict(node.get("data", {}))
                node_id = data.get("id")
                if not node_id:
                    continue
                nodes_payload[node_id] = {"data": data}
            for edge in baseline_snapshot.get("edges", []):
                if not isinstance(edge, Mapping):
                    continue
                data = dict(edge.get("data", {}))
                edge_id = data.get("id")
                if not edge_id:
                    continue
                edges_payload[edge_id] = {"data": data}

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
            value_image: str | None = None,
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
                if value_image is not None:
                    data["value_image"] = value_image
                nodes_payload[node_id] = {"data": data}
            else:
                existing = nodes_payload[node_id]["data"]
                existing.setdefault("meta", {}).update(meta)
                if image is not None and "image" not in existing:
                    existing["image"] = image
                if value_image is not None and "value_image" not in existing:
                    existing["value_image"] = value_image
            return node_id

        for step in all_steps:
            output_node = step.output
            seen_nodes.add(output_node)
            label = self._value_to_label(output_node.value)
            meta_dict_raw = self._sanitize_meta(step.meta)
            meta_dict, meta_images = self._redact_meta_images(meta_dict_raw)
            value_repr = self._trimmed_repr(output_node.value)
            image_src = None
            if PILImage is not None and isinstance(output_node.value, PILImage):
                image_src = self._image_to_data_url(output_node.value)
            elif np is not None and self._ndarray_is_image_like(output_node.value):
                image_src = self._ndarray_to_data_url(output_node.value)

            target_id = ensure_node(
                output_node,
                label,
                value_repr,
                meta_dict,
                image=image_src,
                value_image=image_src,
            )
            target_node_data = nodes_payload[target_id]["data"]
            if meta_images:
                meta_image_list = target_node_data.setdefault("meta_images", [])
                for candidate in meta_images:
                    if candidate not in meta_image_list:
                        meta_image_list.append(candidate)
                if ("image" not in target_node_data or not target_node_data["image"]) and meta_image_list:
                    target_node_data["image"] = meta_image_list[0]
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
                edge_key = (input_node, output_node)
                edge_id = self._edge_ids.get(edge_key)
                if edge_id is None:
                    edge_id = f"edge-{next(self._edge_counter)}"
                    self._edge_ids[edge_key] = edge_id
                edges_payload[edge_id] = {
                    "data": {"id": edge_id, "source": source_id, "target": target_id}
                }

        if not merge:
            for node in list(self._node_ids):
                if node not in seen_nodes:
                    del self._node_ids[node]

        if not merge:
            active_edge_keys = {
                key for key, edge_id in self._edge_ids.items() if edge_id in edges_payload
            }
            self._edge_ids = {
                key: edge_id for key, edge_id in self._edge_ids.items() if key in active_edge_keys
            }

        if final_node_id is None and graph.last.output in self._node_ids:
            final_node_id = self._node_ids[graph.last.output]

        if final_node_id is not None and final_node_id in nodes_payload:
            final_node = nodes_payload[final_node_id]["data"]
            final_node["is_last"] = True
            if mark_success:
                final_node["is_success"] = True

        nodes_list = list(nodes_payload.values())
        edges_list = list(edges_payload.values())
        snapshot = {"type": "snapshot", "nodes": nodes_list, "edges": edges_list}
        self._latest_snapshot = snapshot

        if final_node_id is not None:
            self._last_node_id = final_node_id
        elif not nodes_list:
            self._last_node_id = None

        self._dispatch_snapshot(nodes_list, edges_list, mark_success=mark_success)

    def publish_error(self, error: BaseException, effect: Effect | None) -> None:
        """Publish an error node with stack trace information."""
        snapshot = self._latest_snapshot or self._stream.last_snapshot()
        nodes_map: dict[str, dict[str, Any]] = {}
        for node in snapshot.get("nodes", []):
            data = dict(node.get("data", {}))
            node_id = data.get("id")
            if not node_id:
                continue
            nodes_map[node_id] = {"data": data}
        edges: list[dict[str, Any]] = []
        for edge in snapshot.get("edges", []):
            if not isinstance(edge, Mapping):
                continue
            edge_payload: dict[str, Any] = {"data": dict(edge.get("data", {}))}
            for key, value in edge.items():
                if key == "data":
                    continue
                edge_payload[key] = value
            edges.append(edge_payload)

        node_id = f"node-{next(self._node_counter)}"
        label = f"Error: {type(error).__name__}"
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
            meta["trace"] = ''.join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )

        data = {
            "id": node_id,
            "label": label,
            "value_repr": meta["message"],
            "meta": meta,
            "is_error": True,
        }
        nodes_map[node_id] = {"data": data}

        if self._last_node_id and self._last_node_id in nodes_map:
            edge_id = f"edge-{next(self._edge_counter)}"
            edges.append({"data": {"id": edge_id, "source": self._last_node_id, "target": node_id}})

        nodes_list = list(nodes_map.values())
        self._last_node_id = node_id
        self._latest_snapshot = {"type": "snapshot", "nodes": nodes_list, "edges": edges}
        self._dispatch_snapshot(nodes_list, edges, force=True)


    def _dispatch_snapshot(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        *,
        mark_success: bool = False,
        force: bool = False,
    ) -> None:
        if mark_success:
            force = True

        interval = self._throttle_interval
        publish_now = False
        timer_to_cancel: threading.Timer | None = None

        with self._lock:
            if interval <= 0 or force:
                publish_now = True
                timer_to_cancel = self._cancel_pending_timer_locked()
                self._pending_snapshot = None
                self._last_publish_time = time.monotonic()
            else:
                now = time.monotonic()
                elapsed = now - self._last_publish_time
                if elapsed >= interval:
                    publish_now = True
                    timer_to_cancel = self._cancel_pending_timer_locked()
                    self._pending_snapshot = None
                    self._last_publish_time = now
                else:
                    delay = max(0.01, interval - elapsed)
                    self._pending_snapshot = (nodes, edges, mark_success)
                    if self._pending_timer is None:
                        self._pending_timer = threading.Timer(delay, self._flush_pending_snapshot)
                        self._pending_timer.daemon = True
                        self._pending_timer.start()

        if timer_to_cancel is not None:
            timer_to_cancel.cancel()
        if publish_now:
            self._stream.publish_snapshot(nodes, edges)

    def _cancel_pending_timer_locked(self) -> threading.Timer | None:
        timer = self._pending_timer
        if timer is not None:
            self._pending_timer = None
        return timer

    def _flush_pending_snapshot(self) -> None:
        with self._lock:
            payload = self._pending_snapshot
            self._pending_snapshot = None
            self._pending_timer = None
            if not payload:
                return
            nodes, edges, _ = payload
            self._last_publish_time = time.monotonic()
        self._stream.publish_snapshot(nodes, edges)

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

        mode: str | None
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
  </head>
  <body>
    <div id=\"cy\"></div>
    <div id=\"details\">
      <h2>Node Details</h2>
      <div id=\"details-content\">
        <div id=\"details-json\">Select a node to inspect its metadata.</div>
        <div id=\"details-images\"></div>
      </div>
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
        .selector('node[image][is_success]')
        .style({
          'border-color': '#166534',
          'border-width': 3
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
