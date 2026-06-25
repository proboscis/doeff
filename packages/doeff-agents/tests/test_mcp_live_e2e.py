"""Live E2E test: Launch Claude Code with MCP tools via real tmux.

Verifies that Claude Code actually connects to the MCP SSE server
and can call tools through it.

Requires: tmux, claude CLI (authenticated), network.
Run with: uv run pytest packages/doeff-agents/tests/test_mcp_live_e2e.py -v -s
"""

import logging
import shutil
from pathlib import Path
from tempfile import mkdtemp

import pytest
from doeff_agents.adapters.base import AgentType
from doeff_agents.effects import (
    Capture,
    LaunchEffect,
    Monitor,
    Stop,
)
from doeff_agents.handlers import _agent_handler_defhandler
from doeff_agents.handlers.production import TmuxAgentHandler
from doeff_agents.tmux import TmuxSessionBackend
from doeff_core_effects.handlers import state
from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay, sync_time_handler

from doeff import Perform, do, run
from doeff.mcp import McpParamSchema, McpToolDef

logging.basicConfig(level=logging.INFO)

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


def _require_live_dependency(binary: str) -> None:
    assert shutil.which(binary) is not None, (
        f"{binary!r} is required for the Claude MCP live E2E test"
    )


# -- Test --------------------------------------------------------------------

@pytest.mark.e2e
class TestMcpLiveE2E:
    def test_claude_calls_mcp_tool(self):
        """Claude Code launches, discovers MCP tools, and calls echo tool."""
        _require_live_dependency("tmux")
        _require_live_dependency("claude")

        work_dir = Path(mkdtemp(prefix="doeff-mcp-e2e-"))
        _tool_call_log.clear()

        handler = TmuxAgentHandler(backend=TmuxSessionBackend())
        agent_defhandler = _agent_handler_defhandler(handler)

        @do
        def program():
            handle = yield Perform(
                LaunchEffect(
                    session_name="mcp-live-e2e",
                    agent_type=AgentType.CLAUDE,
                    work_dir=work_dir,
                    prompt=(
                        'You have an MCP tool called "doeff-echo". '
                        'Call it with the message "hello-from-mcp".'
                    ),
                    mcp_tools=(echo_tool,),
                )
            )

            # Wait for Claude to boot, then poll until tool is called or timeout
            for _ in range(90):  # 90 sec max
                yield Delay(1.0)
                if len(_tool_call_log) > 0:
                    # Tool was called — wait a bit for Claude to finish
                    yield Delay(3.0)
                    break
                obs = yield Monitor(handle)
                if obs.is_terminal:
                    break

            output = yield Capture(handle, lines=200)
            yield Stop(handle)
            return output

        try:
            output = run(scheduled(state()(sync_time_handler()(agent_defhandler(program())))))

            # Verify the tool was actually called via the in-process log
            assert len(_tool_call_log) > 0, (
                f"MCP tool was never called by Claude Code.\n"
                f"Session output:\n{output}"
            )
            assert _tool_call_log[0]["message"] == "hello-from-mcp", (
                f"Tool called with wrong message: {_tool_call_log}\n"
                f"Session output:\n{output}"
            )
            print("\n=== SUCCESS ===")
            print(f"Tool calls: {_tool_call_log}")
            print(f"Output tail:\n{output[-500:]}")
        finally:
            # Cleanup tmux session if still running
            import subprocess
            subprocess.run(
                ["tmux", "kill-session", "-t", "mcp-live-e2e"],
                capture_output=True,
                check=False,
            )
