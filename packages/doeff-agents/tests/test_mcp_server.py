"""Tests for MCP SSE server and Launch integration."""

import http.client
import json

import pytest
from doeff_agents.mcp_server import McpToolServer

from doeff import do, run
from doeff.mcp import McpParamSchema, McpToolDef

# -- Helpers -----------------------------------------------------------------

def _make_echo_tool():
    @do
    def echo_handler(msg):
        return {"echo": msg}

    return McpToolDef(
        name="echo",
        description="Echo the message back",
        params=(McpParamSchema(name="msg", type="string", description="Message to echo"),),
        handler=echo_handler,
    )


def _make_add_tool():
    @do
    def add_handler(a, b):
        return a + b

    return McpToolDef(
        name="add",
        description="Add two numbers",
        params=(
            McpParamSchema(name="a", type="integer", description="First number"),
            McpParamSchema(name="b", type="integer", description="Second number"),
        ),
        handler=add_handler,
    )


def _simple_run_tool(tool, arguments):
    """Simple test-side tool execution used by the fake VM wakeup."""
    args = [arguments.get(name) for name in tool.param_names()]
    return run(tool.handler(*args))


class _FakeVmWakeup:
    """Resolve one queued MCP request as if mcp_server_loop ran in the VM."""

    def __init__(self, server: McpToolServer):
        self.server = server

    def complete(self, _value):
        req = self.server.request_queue.get(timeout=1)
        try:
            result = _simple_run_tool(self.server._tools[req.tool_name], req.arguments)
        except Exception as exc:  # pragma: no cover - defensive test helper
            req.holder.append((False, str(exc)))
        else:
            req.holder.append((True, result))
        req.event.set()


def _prime_fake_vm(server: McpToolServer) -> None:
    server.wakeup_mailbox.put(_FakeVmWakeup(server))


# -- JSON-RPC dispatch tests -------------------------------------------------

class TestJsonRpcDispatch:
    def test_initialize(self):
        server = McpToolServer(tools=(_make_echo_tool(),))
        resp = server.dispatch_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert resp["result"]["protocolVersion"] == "2024-11-05"
        assert resp["result"]["capabilities"]["tools"] == {}
        server.server_close()

    def test_tools_list(self):
        server = McpToolServer(tools=(_make_echo_tool(), _make_add_tool()))
        resp = server.dispatch_jsonrpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = resp["result"]["tools"]
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert names == {"echo", "add"}
        # Check inputSchema
        echo_tool = next(t for t in tools if t["name"] == "echo")
        assert echo_tool["inputSchema"]["properties"]["msg"]["type"] == "string"
        server.server_close()

    def test_tools_call(self):
        server = McpToolServer(tools=(_make_echo_tool(),))
        _prime_fake_vm(server)
        resp = server.dispatch_jsonrpc({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "echo", "arguments": {"msg": "hello"}},
        })
        content = resp["result"]["content"][0]
        assert content["type"] == "text"
        assert json.loads(content["text"]) == {"echo": "hello"}
        assert resp["result"]["isError"] is False
        server.server_close()

    def test_tools_call_add(self):
        server = McpToolServer(tools=(_make_add_tool(),))
        _prime_fake_vm(server)
        resp = server.dispatch_jsonrpc({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "add", "arguments": {"a": 3, "b": 7}},
        })
        assert resp["result"]["content"][0]["text"] == "10"
        server.server_close()

    def test_unknown_tool(self):
        server = McpToolServer(tools=(_make_echo_tool(),))
        resp = server.dispatch_jsonrpc({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "nonexistent", "arguments": {}},
        })
        assert "error" in resp
        assert resp["error"]["code"] == -32602
        server.server_close()

    def test_unknown_method(self):
        server = McpToolServer(tools=())
        resp = server.dispatch_jsonrpc({"jsonrpc": "2.0", "id": 6, "method": "foo/bar"})
        assert resp["error"]["code"] == -32601
        server.server_close()

    def test_notification_returns_none(self):
        server = McpToolServer(tools=())
        resp = server.dispatch_jsonrpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert resp is None
        server.server_close()

    def test_ping(self):
        server = McpToolServer(tools=())
        resp = server.dispatch_jsonrpc({"jsonrpc": "2.0", "id": 7, "method": "ping"})
        assert resp["result"] == {}
        server.server_close()


# -- Server lifecycle tests --------------------------------------------------

class TestServerLifecycle:
    def test_direct_callback_dispatch_is_not_supported(self):
        with pytest.raises(TypeError):
            McpToolServer(
                tools=(_make_echo_tool(),),
                run_tool=lambda *_args: {"bad": "separate-vm"},
            )

    def test_start_and_shutdown(self):
        server = McpToolServer(tools=(_make_echo_tool(),))
        server.start()
        assert server.port > 0
        assert "127.0.0.1" in server.url
        assert "/sse" in server.url
        server.shutdown()

    def test_auto_port_assignment(self):
        server = McpToolServer(tools=(), port=0)
        server.start()
        assert server.port > 0
        server.shutdown()

    def test_ready_signal_completion_failure_aborts_startup(self, monkeypatch):
        class FailingReadyPromise:
            def complete(self, _value):
                raise RuntimeError("ready already completed")

        server = McpToolServer(tools=(), port=0)
        server._ready_promise = FailingReadyPromise()

        def serve_forever(_poll_interval=0.5):
            raise AssertionError("serve_forever should not start after ready failure")

        monkeypatch.setattr(server, "serve_forever", serve_forever)
        try:
            with pytest.raises(RuntimeError, match="ready already completed"):
                server._serve_with_ready_signal()
        finally:
            server.server_close()

    def test_client_disconnect_does_not_print_traceback(self, capsys):
        server = McpToolServer(tools=(), port=0)
        try:
            try:
                raise ConnectionResetError("client closed SSE connection")
            except ConnectionResetError:
                server.handle_error(object(), ("127.0.0.1", 12345))

            captured = capsys.readouterr()
            assert "Exception occurred during processing of request" not in captured.err
            assert "ConnectionResetError" not in captured.err
        finally:
            server.server_close()


# -- SSE HTTP transport tests ------------------------------------------------

class TestSseTransport:
    def test_sse_endpoint_event(self):
        """GET /sse returns endpoint event with session-specific POST URL."""
        server = McpToolServer(tools=(_make_echo_tool(),))
        server.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn.request("GET", "/sse")
            resp = conn.getresponse()
            assert resp.status == 200

            # Read the endpoint event
            buf = ""
            while not buf.endswith("\n\n"):
                ch = resp.read(1).decode()
                buf += ch

            lines = buf.strip().split("\n")
            event_type = None
            data = None
            for line in lines:
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data = line.split(":", 1)[1].strip()

            assert event_type == "endpoint"
            assert data.startswith("/message?sessionId=")
            conn.close()
        finally:
            server.shutdown()

    def test_full_sse_flow(self):
        """Complete SSE flow: connect → POST tools/list → read response."""
        server = McpToolServer(tools=(_make_echo_tool(),))
        server.start()
        try:
            # 1. Connect SSE
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn.request("GET", "/sse")
            resp = conn.getresponse()

            # 2. Read endpoint event
            endpoint = self._read_sse_data(resp)

            # 3. POST tools/list
            conn2 = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            conn2.request("POST", endpoint, body.encode(), {"Content-Type": "application/json"})
            post_resp = conn2.getresponse()
            assert post_resp.status == 202
            post_resp.read()
            conn2.close()

            # 4. Read JSON-RPC response from SSE
            data = json.loads(self._read_sse_data(resp))
            assert data["id"] == 1
            assert len(data["result"]["tools"]) == 1
            assert data["result"]["tools"][0]["name"] == "echo"

            conn.close()
        finally:
            server.shutdown()

    def test_sse_tool_call(self):
        """Complete tool call over SSE transport."""
        server = McpToolServer(tools=(_make_echo_tool(),))
        server.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn.request("GET", "/sse")
            resp = conn.getresponse()
            endpoint = self._read_sse_data(resp)
            _prime_fake_vm(server)

            # POST tools/call
            conn2 = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            body = json.dumps({
                "jsonrpc": "2.0", "id": 42, "method": "tools/call",
                "params": {"name": "echo", "arguments": {"msg": "test-msg"}},
            })
            conn2.request("POST", endpoint, body.encode(), {"Content-Type": "application/json"})
            conn2.getresponse().read()
            conn2.close()

            data = json.loads(self._read_sse_data(resp))
            assert data["id"] == 42
            result_text = data["result"]["content"][0]["text"]
            assert json.loads(result_text) == {"echo": "test-msg"}
            conn.close()
        finally:
            server.shutdown()

    @staticmethod
    def _read_sse_data(resp) -> str:
        """Read one SSE event and return the data field."""
        buf = ""
        while not buf.endswith("\n\n"):
            ch = resp.read(1).decode()
            buf += ch
        for line in buf.strip().split("\n"):
            if line.startswith("data:"):
                return line.split(":", 1)[1].strip()
        raise ValueError(f"No data field in SSE event: {buf!r}")
