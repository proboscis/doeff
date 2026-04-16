"""Live E2E test: Launch Claude Code with MCP tools via real tmux.

Verifies that Claude Code actually connects to the MCP SSE server
and can call tools through it.

Requires: tmux, claude CLI (authenticated), network.
Run with: uv run pytest packages/doeff-agents/tests/test_mcp_live_e2e.py -v -s
"""

import json
import logging
import shutil
import time
from pathlib import Path
from tempfile import mkdtemp

import pytest

from doeff import Perform, WithHandler, do, run
from doeff.mcp import McpParamSchema, McpToolDef
from doeff_agents.adapters.base import AgentType, LaunchConfig
from doeff_agents.effects import (
    Capture,
    LaunchEffect,
    Monitor,
    Send,
    Sleep,
    Stop,
)
from doeff_agents.handlers import _make_protocol_handler
from doeff_agents.handlers.production import TmuxAgentHandler
from doeff_agents.monitor import SessionStatus

logging.basicConfig(level=logging.INFO)

# Skip entire module if tmux or claude are not available
pytestmark = pytest.mark.skipif(
    shutil.which("claude") is None or shutil.which("tmux") is None,
    reason="Requires tmux and claude CLI",
)

# -- Tools -------------------------------------------------------------------

_tool_call_log: list[dict] = []


@do
def _echo_handler(message):
    _tool_call_log.append({"tool": "echo", "message": message})
    return f"ECHO_RESPONSE: {message}"


echo_tool = McpToolDef(
    name="doeff-echo",
    description="Echo the message back. Use this tool when asked to echo something.",
    params=(McpParamSchema(name="message", type="string", description="The message to echo back"),),
    handler=_echo_handler,
)


# -- Test --------------------------------------------------------------------

class TestMcpLiveE2E:
    def test_claude_calls_mcp_tool(self):
        """Claude Code launches, discovers MCP tools, and calls echo tool."""
        work_dir = Path(mkdtemp(prefix="doeff-mcp-e2e-"))
        _tool_call_log.clear()

        handler = TmuxAgentHandler()
        agent_protocol = _make_protocol_handler(handler)

        @do
        def program():
            config = LaunchConfig(
                agent_type=AgentType.CLAUDE,
                work_dir=work_dir,
                prompt=(
                    'You have an MCP tool called "doeff-echo". '
                    'Call it with the message "hello-from-mcp".'
                ),
                mcp_tools=(echo_tool,),
            )
            handle = yield Perform(
                LaunchEffect(session_name="mcp-live-e2e", config=config)
            )

            # Wait for Claude to boot, then poll until tool is called or timeout
            for _ in range(90):  # 90 sec max
                yield Sleep(1.0)
                if len(_tool_call_log) > 0:
                    # Tool was called — wait a bit for Claude to finish
                    yield Sleep(3.0)
                    break
                obs = yield Monitor(handle)
                if obs.is_terminal:
                    break

            output = yield Capture(handle, lines=200)
            yield Stop(handle)
            return output

        try:
            output = run(WithHandler(agent_protocol, program()))

            # Verify the tool was actually called via the in-process log
            assert len(_tool_call_log) > 0, (
                f"MCP tool was never called by Claude Code.\n"
                f"Session output:\n{output}"
            )
            assert _tool_call_log[0]["message"] == "hello-from-mcp", (
                f"Tool called with wrong message: {_tool_call_log}\n"
                f"Session output:\n{output}"
            )
            print(f"\n=== SUCCESS ===")
            print(f"Tool calls: {_tool_call_log}")
            print(f"Output tail:\n{output[-500:]}")
        finally:
            # Cleanup tmux session if still running
            import subprocess
            subprocess.run(
                ["tmux", "kill-session", "-t", "mcp-live-e2e"],
                capture_output=True,
            )
            # Cleanup MCP server
            if hasattr(handler, '_mcp_servers'):
                for server in handler._mcp_servers.values():
                    server.shutdown()
