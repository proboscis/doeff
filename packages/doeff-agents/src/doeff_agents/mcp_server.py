"""Minimal MCP SSE server for exposing doeff tools to agents.

Implements the MCP SSE transport:
  - GET /sse    → SSE stream (sends endpoint event, then JSON-RPC responses)
  - POST /message?sessionId=X → receives JSON-RPC requests

Supports: initialize, tools/list, tools/call, ping.

Two dispatch modes:

1. Queue-based (preferred, for doeff-native flow): HTTP thread pushes
   McpToolRequest onto self.request_queue and completes an ExternalPromise
   on the mailbox to wake the VM. VM's mcp-server-loop task drains the queue
   and runs tools inside the same VM as the caller. HTTP thread blocks on
   the request's threading.Event until the VM sets the result.

2. Direct callback (legacy, backward-compat): caller supplies run_tool fn;
   HTTP thread calls it synchronously. Used by handlers/production.py and
   handlers/testing.py (OOP path).

Usage (queue mode):
    server = McpToolServer(tools, port=0)
    server.start(ready_promise=ep)          # ep completes when ready
    # VM: yield Spawn(mcp-server-loop server full-stack)

Usage (legacy):
    server = McpToolServer(tools, run_tool=fn, port=0)
    server.start()
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from dataclasses import dataclass
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from doeff.mcp import McpToolDef

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "doeff-mcp", "version": "0.1.0"}

# Timeouts — tunable via module constants (not McpToolServer args, to keep API small)
TOOL_DISPATCH_WAKEUP_TIMEOUT = 5.0    # HTTP thread waits this long for VM to post wakeup ep
TOOL_RESPONSE_TIMEOUT = 120.0          # HTTP thread waits this long for VM to produce result


@dataclass
class McpToolRequest:
    """Tool invocation crossing the HTTP thread → VM boundary.

    HTTP thread creates, VM resolves. event + holder form a single-shot
    channel: VM writes holder[0] then sets event; HTTP thread .wait()s then
    reads holder[0]. Errors are represented as (False, error_message);
    success as (True, result_value).
    """

    tool_name: str
    arguments: dict[str, Any]
    event: threading.Event
    holder: list  # [(ok: bool, value_or_error)]


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
        run_tool: (legacy) direct callback for synchronous dispatch. If
            provided, tools/call is handled inline in the HTTP thread. If
            None (the doeff-native flow), tools/call pushes requests onto
            self.request_queue and wakes the VM via self.wakeup_mailbox.
        port: Port to bind (0 = auto-assign).
    """

    def __init__(
        self,
        tools: tuple[McpToolDef, ...],
        *,
        run_tool: RunToolFn | None = None,
        port: int = 0,
    ) -> None:
        self._tools = {t.name: t for t in tools}
        self._run_tool = run_tool
        self.sessions: dict[str, _SseSession] = {}
        self.shutting_down = False
        self._thread: threading.Thread | None = None

        # Queue-based dispatch state (active when run_tool is None)
        # request_queue: HTTP thread → VM (tool invocations)
        # wakeup_mailbox: VM → HTTP thread (single-slot ExternalPromise the
        #   HTTP thread completes to wake the VM's mcp-server-loop)
        self.request_queue: queue.Queue[McpToolRequest] = queue.Queue()
        self.wakeup_mailbox: queue.Queue[Any] = queue.Queue(maxsize=1)

        # Ready signaling — set by start() if the caller provides an
        # ExternalPromise so the VM can Wait on server readiness.
        self._ready_promise: Any | None = None

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

    def start(self, *, ready_promise: Any | None = None) -> None:
        """Start serving in a daemon thread.

        If ready_promise is provided (typically an ExternalPromise), it is
        completed with None once serve_forever has entered its accept loop.
        The VM can Wait on ready_promise.future to avoid a race where the
        agent process starts before the HTTP server can accept connections.
        """
        self._ready_promise = ready_promise
        self._thread = threading.Thread(
            target=self._serve_with_ready_signal,
            name="doeff-mcp-server",
            daemon=True,
        )
        self._thread.start()
        log.info("MCP server started at %s", self.url)

    def _serve_with_ready_signal(self) -> None:
        """Serve loop wrapper that signals readiness once accepting."""
        if self._ready_promise is not None:
            try:
                self._ready_promise.complete(None)
            except Exception:
                log.exception("Failed to complete ready_promise")
        self.serve_forever()

    def shutdown(self) -> None:
        """Stop the server and close all SSE sessions.

        Also wakes up any VM task blocked on wakeup_mailbox by completing
        the wakeup promise (if one is posted) so mcp-server-loop can
        observe self.shutting_down and exit cleanly.
        """
        self.shutting_down = True
        # Signal all sessions to close
        for session in list(self.sessions.values()):
            session.queue.put(None)
        # Wake the VM loop — block briefly for the next wakeup ep to arrive
        # so we don't race against a VM iteration that's about to post one.
        try:
            wakeup_ep = self.wakeup_mailbox.get(timeout=5.0)
            wakeup_ep.complete(None)
        except queue.Empty:
            pass
        except Exception:
            log.exception("Failed to wake VM on shutdown")
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

        run_tool = self._run_tool
        if run_tool is not None:
            # Legacy inline dispatch path
            try:
                result = run_tool(tool, arguments)
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

        # Queue-based dispatch: forward to the VM and block until it resolves
        req = McpToolRequest(
            tool_name=tool_name,
            arguments=arguments,
            event=threading.Event(),
            holder=[],
        )
        self.request_queue.put(req)
        try:
            wakeup_ep = self.wakeup_mailbox.get(timeout=TOOL_DISPATCH_WAKEUP_TIMEOUT)
        except queue.Empty:
            return _jsonrpc_result(msg_id, {
                "content": [{"type": "text", "text": "Error: VM not accepting tool calls"}],
                "isError": True,
            })
        wakeup_ep.complete(None)

        if not req.event.wait(timeout=TOOL_RESPONSE_TIMEOUT):
            return _jsonrpc_result(msg_id, {
                "content": [{"type": "text", "text": "Error: tool call timed out"}],
                "isError": True,
            })

        ok, value = req.holder[0]
        if not ok:
            return _jsonrpc_result(msg_id, {
                "content": [{"type": "text", "text": f"Error: {value}"}],
                "isError": True,
            })
        text = json.dumps(value) if not isinstance(value, str) else value
        return _jsonrpc_result(msg_id, {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        })


# -- JSON-RPC helpers --------------------------------------------------------

def _jsonrpc_result(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _jsonrpc_error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
