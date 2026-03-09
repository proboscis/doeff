"""Tests for backend-neutral session transport injection."""


from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents import (  # noqa: E402
    AgentType,
    CaptureEffect,
    LaunchConfig,
    LaunchEffect,
    MonitorEffect,
    SendEffect,
    SessionConfig,
    SessionInfo,
    SessionStatus,
    StopEffect,
    TmuxAgentHandler,
    capture_output,
    launch_session,
    monitor_session,
    send_message,
    stop_session,
)
from doeff_agents.adapters.base import InjectionMethod  # noqa: E402
from doeff_agents.session_backend import SessionBackend  # noqa: E402


class FakeAdapter:
    agent_type = AgentType.CLAUDE
    injection_method = InjectionMethod.ARG
    ready_pattern = None
    status_bar_lines = 0

    def launch_command(self, _cfg: LaunchConfig) -> list[str]:
        return ["fake-agent", "--run"]

    def is_available(self) -> bool:
        return True


class FakeBackend(SessionBackend):
    def __init__(self) -> None:
        self.available = True
        self.inside = False
        self.sessions: set[str] = set()
        self.created: list[SessionConfig] = []
        self.sent: list[tuple[str, str, bool, bool]] = []
        self.captures: dict[str, str] = {}
        self.killed: list[str] = []
        self.attached: list[str] = []

    def is_available(self) -> bool:
        return self.available

    def is_inside_session(self) -> bool:
        return self.inside

    def has_session(self, name: str) -> bool:
        return name in self.sessions

    def new_session(self, cfg: SessionConfig) -> SessionInfo:
        self.created.append(cfg)
        self.sessions.add(cfg.session_name)
        pane_id = f"%{cfg.session_name}"
        self.captures[pane_id] = "Goodbye!"
        return SessionInfo(
            session_name=cfg.session_name,
            pane_id=pane_id,
            created_at=datetime.now(timezone.utc),
        )

    def send_keys(
        self,
        target: str,
        keys: str,
        *,
        literal: bool = True,
        enter: bool = True,
    ) -> None:
        self.sent.append((target, keys, literal, enter))

    def capture_pane(
        self,
        target: str,
        lines: int = 100,
        *,
        strip_ansi_codes: bool = True,
    ) -> str:
        return self.captures.get(target, "")

    def kill_session(self, session: str) -> None:
        self.killed.append(session)
        self.sessions.discard(session)

    def attach_session(self, session: str) -> None:
        self.attached.append(session)

    def list_sessions(self) -> list[str]:
        return sorted(self.sessions)


def _config() -> LaunchConfig:
    return LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="hello",
    )


def test_tmux_agent_handler_uses_injected_backend(monkeypatch) -> None:
    backend = FakeBackend()
    monkeypatch.setattr("doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter())

    handler = TmuxAgentHandler(backend=backend)
    launch = LaunchEffect(session_name="worker", config=_config(), ready_timeout=0.1)
    handle = handler.handle_launch(launch)

    assert backend.created[0].session_name == "worker"
    assert backend.sent[0][0] == handle.pane_id
    assert backend.sent[0][1] == "fake-agent --run"

    observation = handler.handle_monitor(MonitorEffect(handle=handle))
    assert observation.status == SessionStatus.DONE

    captured = handler.handle_capture(CaptureEffect(handle=handle, lines=25))
    assert captured == "Goodbye!"

    handler.handle_send(SendEffect(handle=handle, message="continue", enter=True))
    assert backend.sent[-1][1] == "continue"

    handler.handle_stop(StopEffect(handle=handle))
    assert backend.killed == ["worker"]


def test_imperative_session_api_accepts_injected_backend(monkeypatch) -> None:
    backend = FakeBackend()
    monkeypatch.setattr("doeff_agents.session.get_adapter", lambda _agent_type: FakeAdapter())

    session = launch_session("worker", _config(), backend=backend)
    status = monitor_session(session)

    assert status == SessionStatus.DONE

    send_message(session, "ship it")
    assert backend.sent[-1][1] == "ship it"

    assert capture_output(session, 10) == "Goodbye!"

    stop_session(session)
    assert backend.killed == ["worker"]
