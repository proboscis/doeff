"""codex_handler handles LaunchEffect(CODEX) + session lifecycle."""

from pathlib import Path

from doeff_agents.adapters.base import AgentType
from doeff_agents.effects.agent import LaunchEffect, SessionHandle, StopEffect
from doeff_agents.session_backend import SessionBackend
from doeff_core_effects.handlers import lazy_ask, state

from doeff import Perform, do, run


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
        return SessionInfo(
            session_name=cfg.session_name,
            pane_id=pane_id,
            created_at=datetime.now(timezone.utc),
        )

    def send_keys(self, target, keys, *, literal=True, enter=True):
        self.sent_keys.append({"target": target, "keys": keys, "literal": literal})

    def capture_pane(self, target, lines=100, *, strip_ansi_codes=True):
        return self.pane_outputs.get(target, "")

    def kill_session(self, session):
        self.sessions.pop(session, None)


def _run(program, backend):
    from doeff_agents.handlers.codex import codex_handler
    from doeff_core_effects.scheduler import scheduled

    handler = codex_handler(backend=backend)
    wrapped = state()(handler(program))
    return run(scheduled(wrapped))


def test_launch_creates_tmux_session(tmp_path: Path) -> None:
    backend = FakeTmuxBackend()

    @do
    def program():
        return (yield Perform(LaunchEffect(
            session_name="codex-launch",
            agent_type=AgentType.CODEX,
            work_dir=tmp_path,
            prompt="hello",
            model="gpt-5.5",
        )))

    handle = _run(program(), backend)
    assert isinstance(handle, SessionHandle)
    assert handle.session_id == "codex-launch"
    assert not hasattr(handle, "agent_type")
    assert backend.has_session("codex-launch")


def test_codex_handler_asks_for_backend(tmp_path: Path) -> None:
    from doeff_agents.handlers.codex import codex_handler
    from doeff_core_effects.scheduler import scheduled

    backend = FakeTmuxBackend()

    @do
    def program():
        return (yield Perform(LaunchEffect(
            session_name="codex-ask-backend",
            agent_type=AgentType.CODEX,
            work_dir=tmp_path,
            prompt="hello",
        )))

    wrapped = lazy_ask(env={SessionBackend: backend})(state()(codex_handler()(program())))

    handle = run(scheduled(wrapped))

    assert isinstance(handle, SessionHandle)
    assert backend.has_session("codex-ask-backend")


def test_launch_sends_codex_command(tmp_path: Path) -> None:
    backend = FakeTmuxBackend()

    @do
    def program():
        return (yield Perform(LaunchEffect(
            session_name="codex-cmd",
            agent_type=AgentType.CODEX,
            work_dir=tmp_path,
            prompt="do stuff",
            model="gpt-5.5",
        )))

    _run(program(), backend)
    assert len(backend.sent_keys) >= 1
    cmd = backend.sent_keys[0]["keys"]
    assert "codex" in cmd
    assert "gpt-5.5" in cmd


def test_stop_kills_session(tmp_path: Path) -> None:
    backend = FakeTmuxBackend()

    @do
    def program():
        handle = yield Perform(LaunchEffect(
            session_name="codex-stop",
            agent_type=AgentType.CODEX,
            work_dir=tmp_path,
        ))
        assert backend.has_session("codex-stop")
        yield Perform(StopEffect(handle=handle))
        return "stopped"

    result = _run(program(), backend)
    assert result == "stopped"
    assert not backend.has_session("codex-stop")
