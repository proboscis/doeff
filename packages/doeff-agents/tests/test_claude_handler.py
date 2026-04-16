"""Phase 3 TDD: claude_handler handles LaunchEffect(CLAUDE) + session lifecycle."""

import json
from pathlib import Path

from doeff import Perform, WithHandler, do, run
from doeff_core_effects.handlers import state

from doeff_agents.adapters.base import AgentType
from doeff_agents.effects.agent import (
    LaunchEffect,
    SessionHandle,
    SleepEffect,
    StopEffect,
)


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
        self.sent_keys.append({"target": target, "keys": keys, "literal": literal})

    def capture_pane(self, target, lines=100, *, strip_ansi_codes=True):
        return self.pane_outputs.get(target, "")

    def kill_session(self, session):
        self.sessions.pop(session, None)


def _run(program, backend):
    from doeff_agents.handlers.claude import claude_handler
    from doeff_core_effects.scheduler import scheduled
    handler = claude_handler(backend=backend)
    wrapped = WithHandler(state(), WithHandler(handler, program))
    return run(scheduled(wrapped))


class TestClaudeHandlerLaunch:

    def test_launch_creates_tmux_session(self, tmp_path):
        backend = FakeTmuxBackend()

        @do
        def program():
            return (yield Perform(LaunchEffect(
                session_name="test-launch",
                agent_type=AgentType.CLAUDE,
                work_dir=tmp_path,
                prompt="hello",
                model="opus",
            )))

        handle = _run(program(), backend)
        assert isinstance(handle, SessionHandle)
        assert handle.session_name == "test-launch"
        assert handle.agent_type == AgentType.CLAUDE
        assert backend.has_session("test-launch")

    def test_launch_writes_trust(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        backend = FakeTmuxBackend()
        work_dir = tmp_path / "workdir"
        work_dir.mkdir()

        @do
        def program():
            return (yield Perform(LaunchEffect(
                session_name="trust-test",
                agent_type=AgentType.CLAUDE,
                work_dir=work_dir,
                prompt="test",
            )))

        _run(program(), backend)

        claude_json = fake_home / ".claude.json"
        assert claude_json.exists()
        data = json.loads(claude_json.read_text())
        assert str(work_dir.resolve()) in data.get("projects", {})
        project = data["projects"][str(work_dir.resolve())]
        assert project["hasTrustDialogAccepted"] is True

    def test_launch_sends_claude_command(self, tmp_path):
        backend = FakeTmuxBackend()

        @do
        def program():
            return (yield Perform(LaunchEffect(
                session_name="cmd-test",
                agent_type=AgentType.CLAUDE,
                work_dir=tmp_path,
                prompt="do stuff",
                model="opus",
            )))

        _run(program(), backend)
        assert len(backend.sent_keys) >= 1
        cmd = backend.sent_keys[0]["keys"]
        assert "claude" in cmd
        assert "opus" in cmd


class TestClaudeHandlerMcp:

    def test_mcp_server_started(self, tmp_path):
        from doeff.mcp import McpParamSchema, McpToolDef

        backend = FakeTmuxBackend()
        tool = McpToolDef(
            name="test-tool",
            description="test",
            params=(McpParamSchema(name="x", type="string", description="x"),),
            handler=lambda x: x,
        )

        @do
        def program():
            handle = yield Perform(LaunchEffect(
                session_name="mcp-test",
                agent_type=AgentType.CLAUDE,
                work_dir=tmp_path,
                mcp_tools=(tool,),
            ))
            yield Perform(StopEffect(handle=handle))
            return handle

        _run(program(), backend)
        mcp_json = tmp_path / ".mcp.json"
        assert mcp_json.exists()
        config = json.loads(mcp_json.read_text())
        assert "doeff" in config["mcpServers"]


class TestClaudeHandlerStop:

    def test_stop_kills_session(self, tmp_path):
        backend = FakeTmuxBackend()

        @do
        def program():
            handle = yield Perform(LaunchEffect(
                session_name="stop-test",
                agent_type=AgentType.CLAUDE,
                work_dir=tmp_path,
            ))
            assert backend.has_session("stop-test")
            yield Perform(StopEffect(handle=handle))
            return "stopped"

        result = _run(program(), backend)
        assert result == "stopped"
        assert not backend.has_session("stop-test")


class TestClaudeHandlerSleep:

    def test_sleep_effect(self, tmp_path):
        backend = FakeTmuxBackend()

        @do
        def program():
            yield Perform(LaunchEffect(
                session_name="sleep-test",
                agent_type=AgentType.CLAUDE,
                work_dir=tmp_path,
            ))
            yield Perform(SleepEffect(seconds=0.0))
            return "ok"

        result = _run(program(), backend)
        assert result == "ok"
