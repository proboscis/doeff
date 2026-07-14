"""Claude and Codex handlers compose without stealing each other's sessions."""

from pathlib import Path

from doeff_agents.adapters.base import AgentType
from doeff_agents.effects.agent import LaunchEffect, StopEffect
from doeff_core_effects.handlers import state

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
        # Boot straight into a ready REPL frame: the launch paths now
        # gate prompt delivery on the adapter ready_pattern (the codex
        # U+203A composer / claude "shift+tab to cycle" footer).
        self.pane_outputs[pane_id] = (
            "\u203a Ready for input\n"
            "bypass permissions on (shift+tab to cycle)\n"
        )
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


def test_codex_stop_reaches_codex_handler_when_claude_handler_is_inner(tmp_path: Path) -> None:
    from doeff_agents.handlers.claude import claude_handler
    from doeff_agents.handlers.codex import codex_handler
    from doeff_core_effects.scheduler import scheduled

    claude_backend = FakeTmuxBackend()
    codex_backend = FakeTmuxBackend()

    @do
    def program():
        handle = yield Perform(LaunchEffect(
            session_name="codex-mixed",
            agent_type=AgentType.CODEX,
            work_dir=tmp_path,
            prompt="hello",
        ))
        yield Perform(StopEffect(handle=handle))
        return "stopped"

    wrapped = state()(codex_handler(backend=codex_backend)(claude_handler(backend=claude_backend)(program())))

    result = run(scheduled(wrapped))

    assert result == "stopped"
    assert not codex_backend.has_session("codex-mixed")
    assert not claude_backend.sessions
