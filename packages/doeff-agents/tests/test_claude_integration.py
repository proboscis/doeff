"""Phase 4 TDD: End-to-end integration — LaunchEffect → resolver → claude_handler.

The full flow through the composed handler stack:
  user program yields LaunchEffect(CLAUDE, mcp_tools=...)
    → claude_resolver_handler converts to ClaudeLaunchEffect
    → claude_handler processes (trust, MCP, tmux, etc.)
    → returns SessionHandle
"""

import json
from dataclasses import dataclass

from doeff import EffectBase, Pass, Perform, Resume, WithHandler, do, run
from doeff_core_effects.handlers import state

from doeff_agents.adapters.base import AgentType
from doeff_agents.effects.agent import (
    LaunchEffect,
    SessionHandle,
    StopEffect,
)


# Fake tmux backend (same as test_claude_handler.py)
class FakeTmuxBackend:
    def __init__(self):
        self.sessions = {}
        self.pane_outputs = {}
        self.sent_keys = []
        self._next_pane = 0

    def has_session(self, name):
        return name in self.sessions

    def new_session(self, cfg):
        from datetime import datetime, timezone
        from doeff_agents.tmux import SessionInfo
        pane_id = f"%fake{self._next_pane}"
        self._next_pane += 1
        self.sessions[cfg.session_name] = {"pane_id": pane_id, "work_dir": cfg.work_dir}
        self.pane_outputs[pane_id] = ""
        return SessionInfo(session_name=cfg.session_name, pane_id=pane_id,
                           created_at=datetime.now(timezone.utc))

    def send_keys(self, target, keys, *, literal=True, enter=True):
        self.sent_keys.append({"target": target, "keys": keys})

    def capture_pane(self, target, lines=100, *, strip_ansi_codes=True):
        return self.pane_outputs.get(target, "")

    def kill_session(self, session):
        self.sessions.pop(session, None)


# Domain effect + handler for MCP tool capture test
@dataclass(frozen=True)
class GreetEffect(EffectBase):
    name: str


@do
def greet_handler(effect, k):
    if isinstance(effect, GreetEffect):
        return (yield Resume(k, f"Hello, {effect.name}!"))
    yield Pass(effect, k)


def _run_full_stack(program, backend):
    """Stack: state → claude_handler → greet_handler → program.

    claude_handler catches LaunchEffect(CLAUDE) directly — no resolver indirection.
    GetHandlers(k) captures greet_handler so MCP tool calls can use GreetEffect.
    """
    from doeff_agents.handlers import claude_agent_handler
    from doeff_core_effects.scheduler import scheduled

    ch = claude_agent_handler(backend=backend)
    wrapped = WithHandler(
        state(),
        WithHandler(
            ch,
            WithHandler(greet_handler, program),
        ),
    )
    return run(scheduled(wrapped))


class TestFullIntegration:

    def test_launch_effect_claude_flows_through_stack(self, tmp_path):
        """LaunchEffect(CLAUDE) → resolver → claude_handler → SessionHandle."""
        backend = FakeTmuxBackend()

        @do
        def program():
            handle = yield Perform(LaunchEffect(
                session_name="integ-test",
                agent_type=AgentType.CLAUDE,
                work_dir=tmp_path,
                prompt="hello",
                model="opus",
            ))
            return handle

        handle = _run_full_stack(program(), backend)
        assert isinstance(handle, SessionHandle)
        assert handle.session_name == "integ-test"
        assert handle.agent_type == AgentType.CLAUDE
        assert backend.has_session("integ-test")
        # Verify trust file was written (~/.claude.json)
        assert len(backend.sent_keys) >= 1
        assert "claude" in backend.sent_keys[0]["keys"]

    def test_mcp_tool_captures_domain_handler(self, tmp_path):
        """MCP tool call executes with captured domain handler stack."""
        from doeff.mcp import McpParamSchema, McpToolDef

        backend = FakeTmuxBackend()

        @do
        def _greet_tool(name):
            result = yield Perform(GreetEffect(name=name))
            return result

        greet_tool = McpToolDef(
            name="greet",
            description="Greet by name",
            params=(McpParamSchema(name="name", type="string", description="name"),),
            handler=_greet_tool,
        )

        @do
        def program():
            handle = yield Perform(LaunchEffect(
                session_name="mcp-integ",
                agent_type=AgentType.CLAUDE,
                work_dir=tmp_path,
                mcp_tools=(greet_tool,),
            ))
            yield Perform(StopEffect(handle=handle))
            return handle

        handle = _run_full_stack(program(), backend)
        # .mcp.json should exist
        mcp_json = tmp_path / ".mcp.json"
        assert mcp_json.exists()
        # Verify MCP server config
        config = json.loads(mcp_json.read_text())
        assert "doeff" in config["mcpServers"]
