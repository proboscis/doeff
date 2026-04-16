"""Tests for MCP SSE server and Launch integration."""

import json
import http.client
import threading

import pytest

from doeff import do, run, WithHandler, Resume
from doeff.mcp import McpToolDef, McpParamSchema
from doeff_agents.mcp_server import McpToolServer
from doeff_agents.handlers import _make_run_tool


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
    """Simple run_tool that executes without outer handlers."""
    args = [arguments.get(name) for name in tool.param_names()]
    return run(tool.handler(*args))


# -- JSON-RPC dispatch tests -------------------------------------------------

class TestJsonRpcDispatch:
    def test_initialize(self):
        server = McpToolServer(tools=(_make_echo_tool(),), run_tool=_simple_run_tool)
        resp = server.dispatch_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert resp["result"]["protocolVersion"] == "2024-11-05"
        assert resp["result"]["capabilities"]["tools"] == {}
        server.server_close()

    def test_tools_list(self):
        server = McpToolServer(tools=(_make_echo_tool(), _make_add_tool()), run_tool=_simple_run_tool)
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
        server = McpToolServer(tools=(_make_echo_tool(),), run_tool=_simple_run_tool)
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
        server = McpToolServer(tools=(_make_add_tool(),), run_tool=_simple_run_tool)
        resp = server.dispatch_jsonrpc({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "add", "arguments": {"a": 3, "b": 7}},
        })
        assert resp["result"]["content"][0]["text"] == "10"
        server.server_close()

    def test_unknown_tool(self):
        server = McpToolServer(tools=(_make_echo_tool(),), run_tool=_simple_run_tool)
        resp = server.dispatch_jsonrpc({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "nonexistent", "arguments": {}},
        })
        assert "error" in resp
        assert resp["error"]["code"] == -32602
        server.server_close()

    def test_unknown_method(self):
        server = McpToolServer(tools=(), run_tool=_simple_run_tool)
        resp = server.dispatch_jsonrpc({"jsonrpc": "2.0", "id": 6, "method": "foo/bar"})
        assert resp["error"]["code"] == -32601
        server.server_close()

    def test_notification_returns_none(self):
        server = McpToolServer(tools=(), run_tool=_simple_run_tool)
        resp = server.dispatch_jsonrpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert resp is None
        server.server_close()

    def test_ping(self):
        server = McpToolServer(tools=(), run_tool=_simple_run_tool)
        resp = server.dispatch_jsonrpc({"jsonrpc": "2.0", "id": 7, "method": "ping"})
        assert resp["result"] == {}
        server.server_close()


# -- Server lifecycle tests --------------------------------------------------

class TestServerLifecycle:
    def test_start_and_shutdown(self):
        server = McpToolServer(tools=(_make_echo_tool(),), run_tool=_simple_run_tool)
        server.start()
        assert server.port > 0
        assert "127.0.0.1" in server.url
        assert "/sse" in server.url
        server.shutdown()

    def test_auto_port_assignment(self):
        server = McpToolServer(tools=(), run_tool=_simple_run_tool, port=0)
        server.start()
        assert server.port > 0
        server.shutdown()


# -- SSE HTTP transport tests ------------------------------------------------

class TestSseTransport:
    def test_sse_endpoint_event(self):
        """GET /sse returns endpoint event with session-specific POST URL."""
        server = McpToolServer(tools=(_make_echo_tool(),), run_tool=_simple_run_tool)
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
        server = McpToolServer(tools=(_make_echo_tool(),), run_tool=_simple_run_tool)
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
        server = McpToolServer(tools=(_make_echo_tool(),), run_tool=_simple_run_tool)
        server.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn.request("GET", "/sse")
            resp = conn.getresponse()
            endpoint = self._read_sse_data(resp)

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


# -- run_tool with handler stack tests ---------------------------------------

class TestRunToolWithHandlers:
    def test_make_run_tool_no_handlers(self):
        """run_tool works with empty handler list."""
        tool = _make_echo_tool()
        run_tool = _make_run_tool([])
        result = run_tool(tool, {"msg": "hi"})
        assert result == {"echo": "hi"}

    def test_make_run_tool_with_handler(self):
        """run_tool wraps program with captured handlers."""
        from doeff import EffectBase, do, Perform
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class PrefixEffect(EffectBase):
            text: str

        @do
        def prefix_handler(effect, k):
            if isinstance(effect, PrefixEffect):
                return (yield Resume(k, f"[PREFIX] {effect.text}"))
            from doeff import Pass
            yield Pass(effect, k)

        @do
        def tool_handler(msg):
            result = yield Perform(PrefixEffect(text=msg))
            return result

        tool = McpToolDef(
            name="prefixed",
            description="Test tool with custom effect",
            params=(McpParamSchema(name="msg", type="string", description="Msg"),),
            handler=tool_handler,
        )

        run_tool = _make_run_tool([prefix_handler])
        result = run_tool(tool, {"msg": "hello"})
        assert result == "[PREFIX] hello"
