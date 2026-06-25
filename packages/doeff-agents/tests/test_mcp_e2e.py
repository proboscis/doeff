"""E2E tests: Launch with MCP tools through the full doeff handler stack.

These tests use the mock tmux backend but real MCP server, verifying:
1. defmcp-tool → McpToolDef → LaunchEffect.mcp_tools
2. Hy defhandler captures handler stack via GetHandlers()
3. MCP server starts, serves tools, and is reachable over HTTP
4. .mcp.json is written correctly
5. Tool calls are served by the in-VM mcp_server_loop
6. Stopping the session shuts down the MCP server
"""

import http.client
import json
import threading
from dataclasses import dataclass

from doeff_agents.adapters.base import AgentType
from doeff_agents.effects import (
    AgentSpec,
    L2SessionHandle,
    LaunchEffect,
    LaunchSession,
    StopEffect,
)
from doeff_agents.handlers import _agent_handler_defhandler
from doeff_agents.handlers.testing import MockAgentHandler
from doeff_core_effects.handlers import state
from doeff_core_effects.scheduler import CreateExternalPromise, Wait, scheduled

from doeff import (
    EffectBase,
    Perform,
    Resume,
    do,
    run,
)
from doeff import handler as _install_raw_handler
from doeff.mcp import McpParamSchema, McpToolDef

# ---------------------------------------------------------------------------
# Test effects & handlers — simulate a domain-specific handler stack
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GreetEffect(EffectBase):
    name: str


@dataclass(frozen=True)
class UpperEffect(EffectBase):
    text: str


@do
def greet_handler(effect, k):
    """Handler that intercepts GreetEffect."""
    if isinstance(effect, GreetEffect):
        return (yield Resume(k, f"Hello, {effect.name}!"))
    from doeff import Pass
    yield Pass(effect, k)


@do
def upper_handler(effect, k):
    """Handler that intercepts UpperEffect."""
    if isinstance(effect, UpperEffect):
        return (yield Resume(k, effect.text.upper()))
    from doeff import Pass
    yield Pass(effect, k)


# ---------------------------------------------------------------------------
# Tools that use domain effects
# ---------------------------------------------------------------------------

@do
def _greet_tool_handler(name):
    result = yield Perform(GreetEffect(name=name))
    return result


@do
def _upper_tool_handler(text):
    result = yield Perform(UpperEffect(text=text))
    return result


greet_tool = McpToolDef(
    name="greet",
    description="Greet someone by name",
    params=(McpParamSchema(name="name", type="string", description="Person's name"),),
    handler=_greet_tool_handler,
)

upper_tool = McpToolDef(
    name="upper",
    description="Convert text to uppercase",
    params=(McpParamSchema(name="text", type="string", description="Text to convert"),),
    handler=_upper_tool_handler,
)


# ---------------------------------------------------------------------------
# E2E Tests
# ---------------------------------------------------------------------------

class TestMcpE2E:
    """E2E tests exercising the full handler stack with MCP tools."""

    def _run_with_handlers(self, program):
        """Run a doeff program with domain handlers + testing agent handler.

        Agent defhandler is OUTERMOST so that GetHandlers(k) captures
        the domain handlers (greet, upper) from the continuation chain.
        """
        mock_handler = MockAgentHandler()
        agent_defhandler = _agent_handler_defhandler(mock_handler)

        wrapped = agent_defhandler(_install_raw_handler(greet_handler)(_install_raw_handler(upper_handler)(program)))
        return run(scheduled(state()(wrapped))), mock_handler

    def test_l2_launch_session_receives_in_vm_mcp_server_url(self, tmp_path):
        """L2 LaunchSession receives a URL for an in-VM MCP server."""

        class RecordingAgentHandler(MockAgentHandler):
            def __init__(self):
                super().__init__()
                self.mcp_servers = None

            def handle_launch_session(self, effect, mcp_servers=None):
                self.mcp_servers = mcp_servers
                return L2SessionHandle(session_id=effect.spec.session_id)

        agent_handler = RecordingAgentHandler()
        agent_defhandler = _agent_handler_defhandler(agent_handler)

        @do
        def program():
            spec = AgentSpec(
                run_id="run-001",
                node_id="node-mcp",
                attempt=0,
                agent_type=AgentType.CLAUDE,
                work_dir=tmp_path,
                prompt="use tools",
                result_schema={"type": "object"},
                mcp_tools=(greet_tool,),
            )
            return (yield LaunchSession(spec))

        wrapped = agent_defhandler(_install_raw_handler(greet_handler)(program()))
        handle = run(scheduled(state()(wrapped)))

        assert handle.session_id == "run-001-node-mcp-0"
        assert agent_handler.mcp_servers is not None
        assert agent_handler.mcp_servers["doeff"].startswith("http://127.0.0.1:")
        assert agent_handler.mcp_servers["doeff"].endswith("/sse")

    def test_launch_with_mcp_creates_server_and_mcp_json(self, tmp_path):
        """Launch with mcp_tools starts MCP server and writes .mcp.json."""

        @do
        def program():
            handle = yield Perform(
                LaunchEffect(
                    session_name="mcp-test",
                    agent_type=AgentType.CLAUDE,
                    work_dir=tmp_path,
                    prompt="test prompt",
                    mcp_tools=(greet_tool, upper_tool),
                )
            )
            return handle

        _handle, _mock = self._run_with_handlers(program())

        # Verify .mcp.json was written
        mcp_json_path = tmp_path / ".mcp.json"
        assert mcp_json_path.exists(), ".mcp.json should be created"
        mcp_config = json.loads(mcp_json_path.read_text())
        assert "mcpServers" in mcp_config
        assert "doeff" in mcp_config["mcpServers"]
        server_config = mcp_config["mcpServers"]["doeff"]
        assert server_config["type"] == "sse"
        assert server_config["url"].startswith("http://127.0.0.1:")
        assert server_config["url"].endswith("/sse")

    def test_mcp_server_is_reachable_after_launch(self, tmp_path):
        """MCP server responds to HTTP requests after launch."""

        @do
        def program():
            handle = yield Perform(
                LaunchEffect(
                    session_name="mcp-http-test",
                    agent_type=AgentType.CLAUDE,
                    work_dir=tmp_path,
                    mcp_tools=(greet_tool,),
                )
            )
            return handle

        _handle, _mock = self._run_with_handlers(program())

        # Read server URL from .mcp.json
        mcp_config = json.loads((tmp_path / ".mcp.json").read_text())
        url = mcp_config["mcpServers"]["doeff"]["url"]
        # Extract host:port
        from urllib.parse import urlparse
        parsed = urlparse(url)

        # Test health endpoint
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["status"] == "ok"
        conn.close()

    def test_mcp_tool_call_uses_in_vm_server_loop(self, tmp_path):
        """Tool calls execute through the in-VM MCP server loop."""
        observed = []
        errors = []

        @do
        def program():
            handle = yield Perform(
                LaunchEffect(
                    session_name="mcp-stack-test",
                    agent_type=AgentType.CLAUDE,
                    work_dir=tmp_path,
                    mcp_tools=(greet_tool, upper_tool),
                )
            )
            done = yield CreateExternalPromise()

            def call_tools():
                conn = None
                try:
                    mcp_config = json.loads((tmp_path / ".mcp.json").read_text())
                    url = mcp_config["mcpServers"]["doeff"]["url"]
                    from urllib.parse import urlparse

                    parsed = urlparse(url)
                    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
                    conn.request("GET", "/sse")
                    resp = conn.getresponse()
                    endpoint = self._read_sse_data(resp)

                    self._post_jsonrpc(parsed.hostname, parsed.port, endpoint, {
                        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "greet", "arguments": {"name": "doeff"}},
                    })
                    observed.append(json.loads(self._read_sse_data(resp)))

                    self._post_jsonrpc(parsed.hostname, parsed.port, endpoint, {
                        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "upper", "arguments": {"text": "hello world"}},
                    })
                    observed.append(json.loads(self._read_sse_data(resp)))
                except BaseException as exc:  # pragma: no cover - surfaced below
                    errors.append(exc)
                finally:
                    if conn is not None:
                        conn.close()
                    done.complete(None)

            threading.Thread(target=call_tools, daemon=True).start()
            yield Wait(done.future)
            yield Perform(StopEffect(handle=handle))
            return observed

        result, _mock = self._run_with_handlers(program())

        assert errors == []
        assert result[0]["id"] == 1
        assert result[0]["result"]["content"][0]["text"] == "Hello, doeff!"
        assert result[1]["id"] == 2
        assert result[1]["result"]["content"][0]["text"] == "HELLO WORLD"

    def test_tools_list_returns_all_tools(self, tmp_path):
        """tools/list returns all configured MCP tools with correct schemas."""

        @do
        def program():
            handle = yield Perform(
                LaunchEffect(
                    session_name="mcp-list-test",
                    agent_type=AgentType.CLAUDE,
                    work_dir=tmp_path,
                    mcp_tools=(greet_tool, upper_tool),
                )
            )
            return handle

        _handle, _mock = self._run_with_handlers(program())

        mcp_config = json.loads((tmp_path / ".mcp.json").read_text())
        url = mcp_config["mcpServers"]["doeff"]["url"]
        from urllib.parse import urlparse
        parsed = urlparse(url)

        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        conn.request("GET", "/sse")
        resp = conn.getresponse()
        endpoint = self._read_sse_data(resp)

        self._post_jsonrpc(parsed.hostname, parsed.port, endpoint, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/list",
        })
        data = json.loads(self._read_sse_data(resp))
        tools = data["result"]["tools"]
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert names == {"greet", "upper"}

        # Verify inputSchema
        greet = next(t for t in tools if t["name"] == "greet")
        assert greet["inputSchema"]["properties"]["name"]["type"] == "string"
        assert greet["inputSchema"]["required"] == ["name"]

        conn.close()

    def test_launch_without_mcp_tools_no_server(self, tmp_path):
        """Launch without mcp_tools does NOT start MCP server or write .mcp.json."""

        @do
        def program():
            handle = yield Perform(
                LaunchEffect(
                    session_name="no-mcp-test",
                    agent_type=AgentType.CLAUDE,
                    work_dir=tmp_path,
                    prompt="no mcp",
                )
            )
            return handle

        _handle, _mock = self._run_with_handlers(program())

        mcp_json_path = tmp_path / ".mcp.json"
        assert not mcp_json_path.exists(), ".mcp.json should NOT be created"

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _read_sse_data(resp) -> str:
        buf = ""
        while not buf.endswith("\n\n"):
            ch = resp.read(1).decode()
            buf += ch
        for line in buf.strip().split("\n"):
            if line.startswith("data:"):
                return line.split(":", 1)[1].strip()
        raise ValueError(f"No data in SSE event: {buf!r}")

    @staticmethod
    def _post_jsonrpc(host, port, endpoint, msg):
        conn = http.client.HTTPConnection(host, port, timeout=5)
        body = json.dumps(msg).encode()
        conn.request("POST", endpoint, body, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp.status
