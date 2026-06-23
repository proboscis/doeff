"""Tests for backend-neutral session transport injection."""


from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from doeff_core_effects.handlers import lazy_ask

from doeff import do, run

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents import (
    AgentSpec,
    AgentType,
    CaptureEffect,
    CleanupAgentSession,
    GetAgentSession,
    LaunchConfig,
    LaunchEffect,
    LaunchSessionEffect,
    ListAgentSessions,
    MonitorEffect,
    ObserveAgentSession,
    SendEffect,
    SessionConfig,
    SessionInfo,
    SessionStatus,
    StopEffect,
    TmuxAgentHandler,
    agent_effectful_handler,
    capture_output,
    launch_session,
    monitor_session,
    send_message,
    stop_session,
)
from doeff_agents.adapters.base import (
    InjectionMethod,
    LaunchParams,
)
from doeff_agents.adapters.codex import CodexAdapter
from doeff_agents.session_backend import SessionBackend
from doeff_agents.session_store import InMemoryAgentSessionRepository
from doeff_agents.tmux import TmuxSessionBackend


def test_session_api_import_does_not_load_doeff_core() -> None:
    src_path = Path(__file__).resolve().parents[1] / "src"
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(src_path)!r})\n"
        "from doeff_agents.session import launch_session\n"
        "from doeff_agents.tmux import TmuxSessionBackend\n"
        "print(launch_session.__name__)\n"
        "print(TmuxSessionBackend.__name__)\n"
        "print('doeff' in sys.modules)\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [
        "launch_session",
        "TmuxSessionBackend",
        "False",
    ]


class FakeAdapter:
    agent_type = AgentType.CLAUDE
    injection_method = InjectionMethod.ARG
    ready_pattern = None
    status_bar_lines = 0

    def launch_command(self, _params: LaunchParams) -> list[str]:
        return ["fake-agent", "--run"]

    def is_available(self) -> bool:
        return True


class FakeCodexAdapter(CodexAdapter):
    def is_available(self) -> bool:
        return True


class RecordingAdapter(FakeAdapter):
    def __init__(self) -> None:
        self.params: list[LaunchParams] = []

    def launch_command(self, params: LaunchParams) -> list[str]:
        self.params.append(params)
        return ["fake-agent", "--prompt", params.prompt or ""]


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
        self.captures[pane_id] = "$ "
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
        session_env={"PATH": "/agent/bin"},
    )


def test_tmux_agent_handler_uses_injected_backend(monkeypatch) -> None:
    backend = FakeBackend()
    monkeypatch.setattr("doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter())

    handler = TmuxAgentHandler(backend=backend)
    launch = LaunchEffect(
        session_name="worker",
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="hello",
        session_env={"PATH": "/agent/bin"},
        ready_timeout=0.1,
    )
    handle = handler.handle_launch(launch)

    assert backend.created[0].session_name == "worker"
    assert backend.created[0].env is not None
    assert backend.created[0].env["PATH"] == "/agent/bin"
    assert backend.sent[0][0] == "%worker"
    # handle_launch wraps the command with HOME/CLAUDE_HOME exports for
    # AgentType.CLAUDE so the launched agent's `.claude.json` is isolated
    # from any concurrently-running Claude Code instance on the host.
    sent_command = backend.sent[0][1]
    assert "fake-agent --run" in sent_command
    assert "export PATH=/agent/bin;" in sent_command
    assert "export HOME=" in sent_command
    assert "export CLAUDE_HOME=" in sent_command

    observation = handler.handle_monitor(MonitorEffect(handle=handle))
    assert observation.status == SessionStatus.EXITED

    captured = handler.handle_capture(CaptureEffect(handle=handle, lines=25))
    assert captured == "$ "

    handler.handle_send(SendEffect(handle=handle, message="continue", enter=True))
    assert backend.sent[-1][1] == "continue"

    handler.handle_stop(StopEffect(handle=handle))
    assert backend.killed == ["worker"]


def test_tmux_agent_handler_persists_session_state(monkeypatch) -> None:
    backend = FakeBackend()
    repository = InMemoryAgentSessionRepository()
    monkeypatch.setattr("doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter())

    handler = TmuxAgentHandler(backend=backend, session_repository=repository)
    launch = LaunchEffect(
        session_name="persistent-worker",
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="hello",
        ready_timeout=0.1,
    )

    handle = handler.handle_launch(launch)
    snapshot = handler.handle_get_session(GetAgentSession("persistent-worker"))

    assert snapshot is not None
    assert snapshot.session_id == "persistent-worker"
    assert not hasattr(handle, "pane_id")
    assert snapshot.status == SessionStatus.BOOTING

    observed = handler.handle_observe_session(ObserveAgentSession("persistent-worker"))

    assert observed.status == SessionStatus.EXITED
    assert observed.finished_at is not None
    assert observed.output_snippet == "$ "
    assert [
        session.session_id
        for session in handler.handle_list_sessions(ListAgentSessions())
    ] == ["persistent-worker"]

    cleaned = handler.handle_cleanup_session(CleanupAgentSession("persistent-worker"))

    assert cleaned.status == SessionStatus.STOPPED
    assert cleaned.cleaned_at is not None
    assert backend.killed == ["persistent-worker"]


def test_tmux_l2_launch_injects_structured_result_contract(monkeypatch, tmp_path: Path) -> None:
    backend = FakeBackend()
    adapter = RecordingAdapter()
    monkeypatch.setattr("doeff_agents.handlers.production.get_adapter", lambda _agent_type: adapter)

    handler = TmuxAgentHandler(backend=backend)
    spec = AgentSpec(
        run_id="run-structured",
        node_id="node",
        attempt=0,
        agent_type=AgentType.CLAUDE,
        work_dir=tmp_path,
        prompt="Do the domain task only.",
        result_schema={
            "type": "object",
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        },
    )

    handler.handle_launch_session(LaunchSessionEffect(spec=spec))

    assert adapter.params
    prompt = adapter.params[0].prompt or ""
    assert "Do the domain task only." in prompt
    assert "Structured Result Contract (managed by doeff-agents)" in prompt
    assert ".agentd-result.json" in prompt
    assert '"ok"' in prompt
    assert "doeff-agents transport detail" in prompt


def test_tmux_agent_handler_trusts_codex_workspace(monkeypatch, tmp_path: Path) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: FakeCodexAdapter(),
    )
    codex_home = tmp_path / "codex-home"
    work_dir = tmp_path / "workspace"

    handler = TmuxAgentHandler(backend=backend)
    launch = LaunchEffect(
        session_name="codex-worker",
        agent_type=AgentType.CODEX,
        work_dir=work_dir,
        prompt="hello",
        session_env={"CODEX_HOME": str(codex_home)},
    )

    handler.handle_launch(launch)

    assert (codex_home / "config.toml").read_text(encoding="utf-8") == (
        f'[projects."{work_dir}"]\n'
        'trust_level = "trusted"\n'
    )
    assert "export CODEX_HOME=" in backend.sent[0][1]


def test_tmux_agent_handler_injects_codex_mcp_config(monkeypatch, tmp_path: Path) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: FakeCodexAdapter(),
    )

    class FakeMcpServer:
        url = "http://127.0.0.1:51978/sse"

    monkeypatch.setattr(
        TmuxAgentHandler,
        "_start_mcp_server",
        lambda _self, _effect, _run_tool: FakeMcpServer(),
    )

    handler = TmuxAgentHandler(backend=backend)
    launch = LaunchEffect(
        session_name="codex-mcp-worker",
        agent_type=AgentType.CODEX,
        work_dir=tmp_path / "workspace",
        prompt="hello",
        mcp_tools=("hypha-transition-issue",),
        mcp_server_name="hypha",
    )

    handler.handle_launch(launch, run_tool=lambda *_args, **_kwargs: None)

    sent_command = backend.sent[0][1]
    assert 'mcp_servers."hypha".url="http://127.0.0.1:51978/sse"' in sent_command


def test_imperative_session_api_accepts_injected_backend(monkeypatch) -> None:
    backend = FakeBackend()
    monkeypatch.setattr("doeff_agents.session.get_adapter", lambda _agent_type: FakeAdapter())

    session = launch_session("worker", _config(), backend=backend)
    status = monitor_session(session)

    assert backend.created[0].env == {"PATH": "/agent/bin"}
    assert "export PATH=/agent/bin;" in backend.sent[0][1]
    assert status == SessionStatus.EXITED

    send_message(session, "ship it")
    assert backend.sent[-1][1] == "ship it"

    assert capture_output(session, 10) == "$ "

    stop_session(session)
    assert backend.killed == ["worker"]


def test_agent_effectful_handler_asks_for_backend(monkeypatch) -> None:
    backend = FakeBackend()
    monkeypatch.setattr("doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter())

    @do
    def workflow():
        handle = yield LaunchEffect(
            session_name="worker",
            agent_type=AgentType.CLAUDE,
            work_dir=Path.cwd(),
            prompt="hello",
            ready_timeout=0.1,
        )
        observation = yield MonitorEffect(handle=handle)
        yield StopEffect(handle=handle)
        return observation.status

    result = run(
        lazy_ask(env={SessionBackend: backend})(agent_effectful_handler()(workflow()))
    )

    assert result == SessionStatus.EXITED
    assert backend.created[0].session_name == "worker"
    assert backend.killed == ["worker"]


def test_tmux_backend_uses_injected_executable(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[1] == "has-session":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        if args[1] == "new-session":
            return subprocess.CompletedProcess(args, 0, stdout="%42\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("doeff_agents.tmux.subprocess.run", fake_run)

    backend = TmuxSessionBackend(executable=r"C:\msys64\usr\bin\tmux.exe")
    assert backend.is_available()
    info = backend.new_session(SessionConfig(session_name="worker", work_dir=tmp_path))
    backend.send_keys(info.pane_id, "hello")
    backend.capture_pane(info.pane_id)
    backend.kill_session("worker")

    assert calls[0][0] == r"C:\msys64\usr\bin\tmux.exe"
    assert all(call[0] == r"C:\msys64\usr\bin\tmux.exe" for call in calls)


def test_tmux_backend_defaults_to_tmux(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("doeff_agents.tmux.subprocess.run", fake_run)

    backend = TmuxSessionBackend()
    assert backend.is_available()

    assert calls == [["tmux", "-V"]]


def test_tmux_backend_uses_legacy_format_tokens(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[1] == "has-session":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        if args[1] == "new-session":
            return subprocess.CompletedProcess(args, 0, stdout="%42\n", stderr="")
        if args[1] == "list-sessions":
            return subprocess.CompletedProcess(args, 0, stdout="worker\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("doeff_agents.tmux.subprocess.run", fake_run)

    backend = TmuxSessionBackend()
    info = backend.new_session(SessionConfig(session_name="worker"))
    sessions = backend.list_sessions()

    new_session_call = next(call for call in calls if call[1] == "new-session")
    list_sessions_call = next(call for call in calls if call[1] == "list-sessions")

    assert info.pane_id == "%42"
    assert sessions == ["worker"]
    assert new_session_call[new_session_call.index("-F") + 1] == "#D"
    assert list_sessions_call[list_sessions_call.index("-F") + 1] == "#S"


def test_tmux_backend_decodes_text_output_as_utf8(monkeypatch) -> None:
    text_calls: list[dict] = []

    def fake_run(args, **kwargs):
        if kwargs.get("text"):
            text_calls.append(kwargs)
        if args[1] == "has-session":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        if args[1] == "new-session":
            return subprocess.CompletedProcess(args, 0, stdout="%42\n", stderr="")
        if args[1] == "capture-pane":
            return subprocess.CompletedProcess(args, 0, stdout="日本語\n", stderr="")
        if args[1] == "list-sessions":
            return subprocess.CompletedProcess(args, 0, stdout="worker\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("doeff_agents.tmux.subprocess.run", fake_run)

    backend = TmuxSessionBackend()
    info = backend.new_session(SessionConfig(session_name="worker"))
    assert backend.capture_pane(info.pane_id) == "日本語\n"
    assert backend.list_sessions() == ["worker"]

    assert text_calls
    assert all(call["encoding"] == "utf-8" for call in text_calls)
