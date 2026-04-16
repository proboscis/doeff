"""Minimal MCP SSE server for exposing doeff tools to agents.

Implements the MCP SSE transport:
  - GET /sse    → SSE stream (sends endpoint event, then JSON-RPC responses)
  - POST /message?sessionId=X → receives JSON-RPC requests

Supports: initialize, tools/list, tools/call, ping.

Usage:
    server = McpToolServer(tools, run_tool_fn, port=0)
    server.start()  # starts in daemon thread
    print(server.url)  # http://127.0.0.1:<port>/sse
    ...
    server.shutdown()
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from doeff.mcp import McpToolDef

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "doeff-mcp", "version": "0.1.0"}


class _SseSession:
    """Per-client SSE session with a response queue."""

    __slots__ = ("id", "queue")

    def __init__(self) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.queue: queue.Queue[dict | None] = queue.Queue()


class _McpHandler(BaseHTTPRequestHandler):
    """HTTP request handler for MCP SSE transport."""

    server: McpToolServer  # type narrowing

    # Suppress per-request logging
    def log_message(self, format: str, *args: Any) -> None:
        log.debug("MCP %s", format % args)

    def do_GET(self) -> None:
        if self.path == "/sse":
            self._handle_sse()
        elif self.path == "/health":
            self._json_response(200, {"status": "ok"})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/message":
            self._handle_message(parsed.query)
        else:
            self.send_error(404)

    # -- SSE stream ----------------------------------------------------------

    def _handle_sse(self) -> None:
        session = _SseSession()
        self.server.sessions[session.id] = session

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # First event: tell client where to POST messages
        endpoint = f"/message?sessionId={session.id}"
        self._write_sse("endpoint", endpoint)
        log.info("MCP SSE session %s started", session.id)

        try:
            while not self.server.shutting_down:
                try:
                    msg = session.queue.get(timeout=30)
                except queue.Empty:
                    # Send keepalive comment to detect broken connections
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    continue
                if msg is None:  # shutdown signal
                    break
                self._write_sse("message", json.dumps(msg))
        except (BrokenPipeError, ConnectionResetError, OSError):
            log.debug("MCP SSE session %s disconnected", session.id)
        finally:
            self.server.sessions.pop(session.id, None)
            log.info("MCP SSE session %s ended", session.id)

    def _write_sse(self, event: str, data: str) -> None:
        payload = f"event: {event}\ndata: {data}\n\n"
        self.wfile.write(payload.encode())
        self.wfile.flush()

    # -- JSON-RPC message handling -------------------------------------------

    def _handle_message(self, query_string: str) -> None:
        params = parse_qs(query_string)
        session_id = params.get("sessionId", [None])[0]

        session = self.server.sessions.get(session_id) if session_id else None
        if session is None:
            self.send_error(404, "Session not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        response = self.server.dispatch_jsonrpc(body)

        if response is not None:
            session.queue.put(response)

        # Respond with 202 Accepted (response goes via SSE)
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"accepted"}')

    # -- Helpers -------------------------------------------------------------

    def _json_response(self, code: int, obj: Any) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


RunToolFn = Callable[[McpToolDef, dict[str, Any]], Any]


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class McpToolServer(_ThreadingHTTPServer):
    """MCP SSE server that exposes McpToolDef tools.

    Parameters:
        tools: Tuple of McpToolDef to serve.
        run_tool: Callable that executes a tool with arguments.
                  Signature: (tool, arguments_dict) -> result_value
        port: Port to bind (0 = auto-assign).
    """

    def __init__(
        self,
        tools: tuple[McpToolDef, ...],
        run_tool: RunToolFn,
        *,
        port: int = 0,
    ) -> None:
        self._tools = {t.name: t for t in tools}
        self._run_tool = run_tool
        self.sessions: dict[str, _SseSession] = {}
        self.shutting_down = False
        self._thread: threading.Thread | None = None
        super().__init__(("127.0.0.1", port), _McpHandler)

    @property
    def url(self) -> str:
        """SSE endpoint URL for .mcp.json configuration."""
        host, port = self.server_address
        return f"http://{host}:{port}/sse"

    @property
    def port(self) -> int:
        return self.server_address[1]

    # -- Lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Start serving in a daemon thread."""
        self._thread = threading.Thread(
            target=self.serve_forever,
            name="doeff-mcp-server",
            daemon=True,
        )
        self._thread.start()
        log.info("MCP server started at %s", self.url)

    def shutdown(self) -> None:
        """Stop the server and close all SSE sessions."""
        self.shutting_down = True
        # Signal all sessions to close
        for session in list(self.sessions.values()):
            session.queue.put(None)
        super().shutdown()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("MCP server stopped")

    # -- JSON-RPC dispatch ---------------------------------------------------

    def dispatch_jsonrpc(self, msg: dict) -> dict | None:
        """Process a JSON-RPC message and return the response (or None)."""
        method = msg.get("method", "")
        msg_id = msg.get("id")

        if method == "initialize":
            return _jsonrpc_result(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })

        if method == "notifications/initialized":
            return None  # notification — no response

        if method == "ping":
            return _jsonrpc_result(msg_id, {})

        if method == "tools/list":
            tools_list = [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema(),
                }
                for t in self._tools.values()
            ]
            return _jsonrpc_result(msg_id, {"tools": tools_list})

        if method == "tools/call":
            return self._handle_tool_call(msg_id, msg.get("params", {}))

        return _jsonrpc_error(msg_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        tool = self._tools.get(tool_name)
        if tool is None:
            return _jsonrpc_error(msg_id, -32602, f"Unknown tool: {tool_name}")

        try:
            result = self._run_tool(tool, arguments)
            text = json.dumps(result) if not isinstance(result, str) else result
            return _jsonrpc_result(msg_id, {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            })
        except Exception as e:
            log.exception("MCP tool %s failed", tool_name)
            return _jsonrpc_result(msg_id, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            })


# -- JSON-RPC helpers --------------------------------------------------------

def _jsonrpc_result(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _jsonrpc_error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
