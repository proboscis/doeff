"""Tests for backend-neutral session transport injection."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from doeff_core_effects.handlers import lazy_ask, state

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
    default_agent_handler,
    launch_session,
    monitor_session,
    send_message,
    stop_session,
)
from doeff_agents import session_backend as session_backend_module
from doeff_agents.adapters.base import (
    InjectionMethod,
    LaunchParams,
)
from doeff_agents.adapters.codex import CodexAdapter
from doeff_agents.effects import AwaitResultEffect, AwaitStatus, LaunchSession
from doeff_agents.handlers.production import (
    AWAIT_RESULT_CAPTURE_LINES,
    _extract_result_payload,
    _has_complete_result_block,
    _result_contract_prompt,
)
from doeff_agents.result_validation import validate_result_payload
from doeff_agents.runtime import ClaudeRuntimePolicy
from doeff_agents.session_backend import SessionBackend
from doeff_agents.session_store import InMemoryAgentSessionRepository
from doeff_agents.tmux import TmuxSessionBackend, _output_has_unsubmitted_paste_input, strip_ansi

from doeff.mcp import McpParamSchema, McpToolDef


def test_session_api_import_does_not_load_doeff_core() -> None:
    src_path = Path(__file__).resolve().parents[1] / "src"
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(src_path)!r})\n"
        "from doeff_agents.session import launch_session\n"
        "from doeff_agents.session_backend import default_session_backend\n"
        "from doeff_agents.tmux import TmuxSessionBackend\n"
        "print(launch_session.__name__)\n"
        "print(default_session_backend.__name__)\n"
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
        "default_session_backend",
        "TmuxSessionBackend",
        "False",
    ]


def test_default_session_backend_resolves_stable_backend(monkeypatch) -> None:
    monkeypatch.setattr(
        session_backend_module.shutil,
        "which",
        lambda name: "/opt/homebrew/bin/tmux" if name == "tmux" else None,
    )

    backend = session_backend_module.default_session_backend()

    assert isinstance(backend, TmuxSessionBackend)
    assert backend.executable == "/opt/homebrew/bin/tmux"


def test_default_session_backend_requires_available_default(monkeypatch) -> None:
    monkeypatch.setattr(session_backend_module.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="terminal session backend"):
        session_backend_module.default_session_backend()


def test_stable_default_session_backend_caches_availability(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        if args == ["/opt/homebrew/bin/tmux", "-V"]:
            return subprocess.CompletedProcess(args, 0, stdout="tmux 3.6a\n", stderr="")
        if args[:3] == ["/opt/homebrew/bin/tmux", "has-session", "-t"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        raise AssertionError(f"unexpected tmux args: {args}")

    monkeypatch.setattr(
        session_backend_module.shutil,
        "which",
        lambda name: "/opt/homebrew/bin/tmux" if name == "tmux" else None,
    )
    monkeypatch.setattr("doeff_agents.tmux.subprocess.run", fake_run)

    backend = session_backend_module.default_session_backend()

    assert not backend.has_session("missing")
    assert not backend.has_session("missing")
    assert calls.count(["/opt/homebrew/bin/tmux", "-V"]) == 1


class FakeAdapter:
    agent_type = AgentType.CLAUDE
    injection_method = InjectionMethod.TMUX
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
        return ["fake-agent"]


class FakeBackend(SessionBackend):
    def __init__(self) -> None:
        self.available = True
        self.inside = False
        self.sessions: set[str] = set()
        self.created: list[SessionConfig] = []
        self.sent: list[tuple[str, str, bool, bool]] = []
        self.captures: dict[str, str] = {}
        self.transcripts: dict[str, str] = {}
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

    def capture_transcript(
        self,
        target: str,
        lines: int = 100,
        *,
        strip_ansi_codes: bool = True,
    ) -> str:
        text = self.transcripts.get(target, "")
        return "\n".join(text.splitlines()[-lines:])

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
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )

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


def test_tmux_agent_handler_dismisses_claude_mcp_permission_prompt(monkeypatch) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )

    handler = TmuxAgentHandler(backend=backend)
    handle = handler.handle_launch(
        LaunchEffect(
            session_name="worker",
            agent_type=AgentType.CLAUDE,
            work_dir=Path.cwd(),
            prompt="hello",
            ready_timeout=0.1,
        )
    )
    backend.captures["%worker"] = (
        "Tool use\n\n"
        "sbi - sbi-status (MCP)\n"
        "Do you want to proceed?\n"
        "Esc to cancel · Tab to amend\n"
    )

    handler.handle_monitor(MonitorEffect(handle=handle))
    handler.handle_monitor(MonitorEffect(handle=handle))

    proceed_sends = [sent for sent in backend.sent if sent == ("%worker", "", True, True)]
    assert proceed_sends == [("%worker", "", True, True)]


def test_tmux_agent_handler_dismisses_claude_new_mcp_server_prompt(monkeypatch) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )

    handler = TmuxAgentHandler(backend=backend)
    handle = handler.handle_launch(
        LaunchEffect(
            session_name="worker",
            agent_type=AgentType.CLAUDE,
            work_dir=Path.cwd(),
            prompt="hello",
            ready_timeout=0.1,
        )
    )
    backend.captures["%worker"] = (
        "New MCP server found in this project: nak\n\n"
        "MCP servers may execute code or access system resources.\n"
        "All tool calls require approval.\n\n"
        "\u276f 1. Use this MCP server\n"
        "  2. Use this and all future MCP servers in this project\n"
        "  3. Continue without using this MCP server\n\n"
        "Enter to confirm · Esc to cancel\n"
    )

    handler.handle_monitor(MonitorEffect(handle=handle))
    handler.handle_monitor(MonitorEffect(handle=handle))

    proceed_sends = [sent for sent in backend.sent if sent == ("%worker", "", True, True)]
    assert proceed_sends == [("%worker", "", True, True)]


def test_tmux_agent_handler_rejects_anthropic_api_key_session_env(monkeypatch) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )
    forbidden_key = "ANTHROPIC" + "_API_KEY"

    handler = TmuxAgentHandler(backend=backend)
    launch = LaunchEffect(
        session_name="worker",
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="hello",
        session_env={forbidden_key: "secret"},
        ready_timeout=0.1,
    )

    with pytest.raises(ValueError, match="provider API keys"):
        handler.handle_launch(launch)

    assert backend.created == []


def test_tmux_agent_handler_persists_session_state(monkeypatch) -> None:
    backend = FakeBackend()
    repository = InMemoryAgentSessionRepository()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )

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
        session.session_id for session in handler.handle_list_sessions(ListAgentSessions())
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
    assert "DOEFF_AGENT_RESULT_BEGIN" in prompt
    assert "DOEFF_AGENT_RESULT_END" in prompt
    assert "DOEFF_AGENT_RESULT_BEGIN\n{}\nDOEFF_AGENT_RESULT_END" not in prompt
    assert "Do not create JSON result files" in prompt
    assert '"ok"' in prompt
    assert "doeff-agents transport detail" in prompt


def test_tmux_agent_handler_sends_initial_prompt_via_terminal_transport(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    adapter = RecordingAdapter()
    monkeypatch.setattr("doeff_agents.handlers.production.get_adapter", lambda _agent_type: adapter)

    handler = TmuxAgentHandler(backend=backend)
    prompt = "line one\nline two"
    handler.handle_launch(
        LaunchEffect(
            session_name="worker:stdin",
            agent_type=AgentType.CLAUDE,
            work_dir=tmp_path,
            prompt=prompt,
            ready_timeout=0.1,
        )
    )

    sent_command = backend.sent[0][1]
    assert "fake-agent" in sent_command
    assert "line one" not in sent_command
    assert backend.sent[1] == ("%worker:stdin", prompt, True, True)
    assert not list(tmp_path.glob(".agentd-prompt-*.txt"))


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
        f'[projects."{work_dir}"]\ntrust_level = "trusted"\n'
    )
    assert "export CODEX_HOME=" in backend.sent[0][1]


def test_tmux_agent_handler_injects_codex_mcp_config(monkeypatch, tmp_path: Path) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: FakeCodexAdapter(),
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

    handler.handle_launch(
        launch,
        mcp_servers={"hypha": "http://127.0.0.1:51978/sse"},
    )

    sent_command = backend.sent[0][1]
    assert 'mcp_servers."hypha".url="http://127.0.0.1:51978/sse"' in sent_command


def test_l2_launch_session_injects_mcp_config(monkeypatch, tmp_path: Path) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: FakeCodexAdapter(),
    )
    tool = McpToolDef(
        name="sbi-status",
        description="read SBI status",
        params=(McpParamSchema(name="x", type="string", description="unused"),),
        handler=lambda x: x,
    )

    spec = AgentSpec(
        run_id="readiness",
        node_id="sbi-executor",
        attempt=0,
        agent_type=AgentType.CODEX,
        work_dir=tmp_path / "workspace",
        prompt="use the sbi MCP server",
        result_schema={"type": "object"},
        mcp_tools=(tool,),
        mcp_server_name="sbi",
    )

    handle = TmuxAgentHandler(backend=backend).handle_launch_session(
        LaunchSession(spec),
        mcp_servers={"sbi": "http://127.0.0.1:51979/sse"},
    )

    assert handle.session_id == "readiness-sbi-executor-0"
    sent_command = backend.sent[0][1]
    assert 'mcp_servers."sbi".url="http://127.0.0.1:51979/sse"' in sent_command


def test_l2_await_result_prefers_schema_result_block_while_session_still_exists(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    spec = AgentSpec(
        run_id="readiness",
        node_id="sbi-recon",
        attempt=0,
        agent_type=AgentType.CLAUDE,
        work_dir=work_dir,
        prompt="write account state",
        result_schema={"type": "object", "required": ["status"]},
    )
    handler = TmuxAgentHandler(backend=backend)
    handle = handler.handle_launch_session(LaunchSession(spec))
    backend.captures[f"%{handle.session_id}"] = (
        "agent output still visible\n"
        "DOEFF_AGENT_RESULT_BEGIN\n"
        '{"status": "prepared"}\n'
        "DOEFF_AGENT_RESULT_END\n"
    )

    outcome = handler.handle_await_result(AwaitResultEffect(handle=handle, timeout_seconds=0.01))

    assert outcome.status == AwaitStatus.EXITED
    assert outcome.result == {"status": "prepared"}


def test_l2_await_result_uses_extended_capture_before_awaiting_input(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class LineLimitedBackend(FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.capture_lines: list[int] = []

        def capture_pane(
            self,
            target: str,
            lines: int = 100,
            *,
            strip_ansi_codes: bool = True,
        ) -> str:
            self.capture_lines.append(lines)
            text = self.captures.get(target, "")
            return "\n".join(text.splitlines()[-lines:])

    backend = LineLimitedBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    spec = AgentSpec(
        run_id="readiness",
        node_id="sbi-recon",
        attempt=0,
        agent_type=AgentType.CLAUDE,
        work_dir=work_dir,
        prompt="write account state",
        result_schema={"type": "object", "required": ["status"]},
    )
    handler = TmuxAgentHandler(backend=backend)
    handle = handler.handle_launch_session(LaunchSession(spec))
    backend.captures[f"%{handle.session_id}"] = "\n".join(
        [
            "DOEFF_AGENT_RESULT_BEGIN",
            '{"status": "prepared"}',
            "DOEFF_AGENT_RESULT_END",
            *[f"tool noise {idx}" for idx in range(150)],
            "────────────────────────────────────────────────────────────────",
            "\u276f\u00a0",
            "────────────────────────────────────────────────────────────────",
        ]
    )

    outcome = handler.handle_await_result(AwaitResultEffect(handle=handle, timeout_seconds=0.01))

    assert outcome.status == AwaitStatus.EXITED
    assert outcome.result == {"status": "prepared"}
    assert AWAIT_RESULT_CAPTURE_LINES in backend.capture_lines


def test_l2_await_result_uses_transcript_when_result_begin_scrolled_off_screen(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    spec = AgentSpec(
        run_id="readiness",
        node_id="sbi-recon",
        attempt=0,
        agent_type=AgentType.CLAUDE,
        work_dir=work_dir,
        prompt="write account state",
        result_schema={"type": "object", "required": ["status"]},
    )
    handler = TmuxAgentHandler(backend=backend)
    handle = handler.handle_launch_session(LaunchSession(spec))
    pane = f"%{handle.session_id}"
    backend.captures[pane] = (
        '"reason": "long readiness blocker"}\n'
        "DOEFF_AGENT_RESULT_END\n"
        "────────────────────────────────────────────────────────────────\n"
        "\u276f\u00a0retry the shortable inventory query\n"
        "────────────────────────────────────────────────────────────────\n"
    )
    backend.transcripts[pane] = (
        "DOEFF_AGENT_RESULT_BEGIN\n"
        '{"status": "blocked", "reason": "long readiness blocker"}\n'
        "DOEFF_AGENT_RESULT_END\n"
        "────────────────────────────────────────────────────────────────\n"
        "\u276f\u00a0retry the shortable inventory query\n"
        "────────────────────────────────────────────────────────────────\n"
    )

    outcome = handler.handle_await_result(AwaitResultEffect(handle=handle, timeout_seconds=0.01))

    assert outcome.status == AwaitStatus.EXITED
    assert outcome.result == {"status": "blocked", "reason": "long readiness blocker"}


def test_l2_await_result_waits_until_result_end_marker(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    spec = AgentSpec(
        run_id="readiness",
        node_id="sbi-recon",
        attempt=0,
        agent_type=AgentType.CLAUDE,
        work_dir=work_dir,
        prompt="write account state",
        result_schema={"type": "object", "required": ["status"]},
    )
    handler = TmuxAgentHandler(backend=backend)
    handle = handler.handle_launch_session(LaunchSession(spec))
    pane = f"%{handle.session_id}"
    backend.captures[pane] = (
        "DOEFF_AGENT_RESULT_BEGIN\n"
        '{"status": "still-printing"'
    )

    outcome = handler.handle_await_result(AwaitResultEffect(handle=handle, timeout_seconds=0.01))

    assert outcome.status == AwaitStatus.TIMED_OUT
    assert outcome.result is None
    assert outcome.validation_error is None


def test_l2_await_result_ignores_unparseable_raw_transcript_block(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    spec = AgentSpec(
        run_id="readiness",
        node_id="sbi-recon",
        attempt=0,
        agent_type=AgentType.CLAUDE,
        work_dir=work_dir,
        prompt="write account state",
        result_schema={"type": "object", "required": ["status"]},
    )
    handler = TmuxAgentHandler(backend=backend)
    handle = handler.handle_launch_session(LaunchSession(spec))
    pane = f"%{handle.session_id}"
    backend.captures[pane] = (
        '"status": "ok"}\n'
        "DOEFF_AGENT_RESULT_END\n"
        "────────────────────────────────────────────────────────────────\n"
        "\u276f\u00a0"
    )
    backend.transcripts[pane] = (
        "DOEFF_AGENT_RESULT_BEGIN\n"
        "\x1b[48;5;237m"
        '{"status"\x1b[10G "ok"}\n'
        "DOEFF_AGENT_RESULT_END\n"
    )

    outcome = handler.handle_await_result(AwaitResultEffect(handle=handle, timeout_seconds=0.01))

    assert outcome.status == AwaitStatus.TIMED_OUT
    assert outcome.result is None
    assert outcome.validation_error is None


def test_l2_await_result_does_not_treat_claude_status_footer_as_input(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    spec = AgentSpec(
        run_id="readiness",
        node_id="sbi-recon",
        attempt=0,
        agent_type=AgentType.CLAUDE,
        work_dir=work_dir,
        prompt="write account state",
        result_schema={"type": "object", "required": ["status"]},
    )
    handler = TmuxAgentHandler(backend=backend)
    handle = handler.handle_launch_session(LaunchSession(spec))
    backend.captures[f"%{handle.session_id}"] = (
        "⏺ Bash(uv run pytest packages/doeff-agents/tests -q)\n"
        "  ⎿  Running…\n\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "\u276f\u00a0\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents\n"
    )

    outcome = handler.handle_await_result(AwaitResultEffect(handle=handle, timeout_seconds=0.01))

    assert outcome.status == AwaitStatus.TIMED_OUT
    assert outcome.result is None


def test_l2_await_result_does_not_finalize_absent_result_on_live_tmux_shell_prompt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    spec = AgentSpec(
        run_id="readiness",
        node_id="sbi-recon",
        attempt=0,
        agent_type=AgentType.CLAUDE,
        work_dir=work_dir,
        prompt="write account state",
        result_schema={"type": "object", "required": ["status"]},
    )
    handler = TmuxAgentHandler(backend=backend)
    handle = handler.handle_launch_session(LaunchSession(spec))
    pane = f"%{handle.session_id}"
    backend.captures[pane] = (
        "export HOME=/tmp/agent; claude --strict-mcp-config\n"
        "➜  workspace export HOME=/tmp/agent; claude --strict-mcp-config\n"
        "  structured account-state result requested by doeff-agents.\n"
        "  Return the required account-state result.\n\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "\u276f\u00a0\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
    )

    outcome = handler.handle_await_result(AwaitResultEffect(handle=handle, timeout_seconds=0.01))

    assert outcome.status == AwaitStatus.TIMED_OUT
    assert outcome.result is None


def test_l2_await_result_clears_stale_blocked_state_when_current_footer_is_not_input(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    spec = AgentSpec(
        run_id="readiness",
        node_id="sbi-recon",
        attempt=0,
        agent_type=AgentType.CLAUDE,
        work_dir=work_dir,
        prompt="write account state",
        result_schema={"type": "object", "required": ["status"]},
    )
    handler = TmuxAgentHandler(backend=backend)
    handle = handler.handle_launch_session(LaunchSession(spec))
    pane = f"%{handle.session_id}"

    backend.captures[pane] = (
        "No, and tell Claude what to do differently\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "\u276f\u00a0\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents\n"
    )
    handler.handle_monitor(MonitorEffect(handle=handle))
    assert handler.handle_monitor(MonitorEffect(handle=handle)).status == SessionStatus.BLOCKED

    backend.captures[pane] = (
        "⏺ Called sbi\n"
        "  ⎿  Running broker query...\n\n"
        "✻ Marinating… (40s · ↓ tokens)\n\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "\u276f\u00a0\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents\n"
    )
    outcome = handler.handle_await_result(AwaitResultEffect(handle=handle, timeout_seconds=0.01))

    assert outcome.status == AwaitStatus.TIMED_OUT
    assert outcome.result is None


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


def test_imperative_session_api_rejects_anthropic_api_key_session_env(monkeypatch) -> None:
    backend = FakeBackend()
    monkeypatch.setattr("doeff_agents.session.get_adapter", lambda _agent_type: FakeAdapter())
    forbidden_key = "ANTHROPIC" + "_API_KEY"
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="hello",
        session_env={forbidden_key: "secret"},
    )

    with pytest.raises(ValueError, match="provider API keys"):
        launch_session("worker", config, backend=backend)

    assert backend.created == []


def test_agent_effectful_handler_asks_for_backend(monkeypatch) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )

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
        lazy_ask(env={SessionBackend: backend})(state()(agent_effectful_handler()(workflow())))
    )

    assert result == SessionStatus.EXITED
    assert backend.created[0].session_name == "worker"
    assert backend.killed == ["worker"]


def test_agent_effectful_handler_accepts_claude_runtime_policy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )

    @do
    def workflow():
        handle = yield LaunchEffect(
            session_name="worker",
            agent_type=AgentType.CLAUDE,
            work_dir=Path.cwd(),
            prompt="hello",
            ready_timeout=0.1,
        )
        yield StopEffect(handle=handle)
        return backend.sent[0][1]

    policy = ClaudeRuntimePolicy(agent_home=tmp_path)
    command = run(
        lazy_ask(env={SessionBackend: backend})(
            state()(agent_effectful_handler(claude_runtime_policy=policy)(workflow()))
        )
    )

    assert f"export HOME={tmp_path};" in command
    assert f"export CLAUDE_HOME={tmp_path / '.claude'};" in command


def test_default_agent_handler_accepts_claude_runtime_policy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter", lambda _agent_type: FakeAdapter()
    )

    handler = default_agent_handler(
        backend=backend,
        claude_runtime_policy=ClaudeRuntimePolicy(agent_home=tmp_path),
    )
    handle = handler.handle_launch(
        LaunchEffect(
            session_name="worker",
            agent_type=AgentType.CLAUDE,
            work_dir=Path.cwd(),
            prompt="hello",
            ready_timeout=0.1,
        ),
    )
    handler.handle_stop(StopEffect(handle=handle))

    command = backend.sent[0][1]
    assert f"export HOME={tmp_path};" in command
    assert f"export CLAUDE_HOME={tmp_path / '.claude'};" in command


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

    expected_tmux = r"C:\msys64\usr\bin\tmux.exe"
    backend_calls = [call for call in calls if call and call[0] in {expected_tmux, "tmux"}]
    assert backend_calls
    assert all(call[0] == expected_tmux for call in backend_calls)
    capture_calls = [call for call in backend_calls if len(call) > 1 and call[1] == "capture-pane"]
    assert capture_calls
    assert all("-J" in call for call in capture_calls)


def test_tmux_backend_captures_session_transcript_tail(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[1] == "-V":
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[1] == "has-session":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        if args[1] == "new-session":
            return subprocess.CompletedProcess(args, 0, stdout="%42\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("doeff_agents.tmux.subprocess.run", fake_run)
    monkeypatch.setattr("doeff_agents.tmux.tempfile.gettempdir", lambda: str(tmp_path))

    backend = TmuxSessionBackend()
    info = backend.new_session(SessionConfig(session_name="worker"))
    path = backend._transcript_paths[info.pane_id]
    path.write_text(
        'one\nDOEFF_AGENT_RESULT_BEGIN\n{"status":"ok"}\nDOEFF_AGENT_RESULT_END\n',
        encoding="utf-8",
    )

    transcript = backend.capture_transcript(info.pane_id, 3)

    assert "DOEFF_AGENT_RESULT_BEGIN" in transcript
    assert "DOEFF_AGENT_RESULT_END" in transcript
    pipe_calls = [call for call in calls if len(call) > 1 and call[1] == "pipe-pane"]
    assert pipe_calls
    assert str(path) in pipe_calls[0][-1]


def test_result_payload_extract_repairs_wrapped_json_string_lines() -> None:
    output = (
        "done\n"
        "DOEFF_AGENT_RESULT_BEGIN\n"
        "{\n"
        '  "status":"succeeded",\n'
        '  "pr_url":"https://github.com/example/\n'
        '    repo/pull/1",\n'
        '  "pr_head_sha":"abc123",\n'
        '  "branch":"feat/long-\n'
        '    branch"\n'
        "}\n"
        "DOEFF_AGENT_RESULT_END\n"
    )

    payload, error = _extract_result_payload(output)

    assert error is None
    assert isinstance(payload, dict)
    assert payload["pr_url"] == "https://github.com/example/repo/pull/1"
    assert payload["branch"] == "feat/long-branch"


def test_result_payload_extract_repairs_terminal_wraps_inside_json_tokens() -> None:
    output = (
        "done\n"
        "DOEFF_AGENT_RESULT_BEGIN\n"
        '  {"credit_opening_power_jpy":27269135,'
        '"position_notional_jpy":0,'
        '"shortable":{"5\n'
        '  020.T":{"status":"▲","shares":1000\n'
        '  000000},"7011.T":{"status":"◎","shares":1000000000}}}\n'
        "DOEFF_AGENT_RESULT_END\n"
    )

    payload, error = _extract_result_payload(output)

    assert error is None
    assert isinstance(payload, dict)
    assert payload["shortable"]["5020.T"]["shares"] == 1000000000


def test_result_payload_extract_uses_latest_parseable_block() -> None:
    output = (
        "DOEFF_AGENT_RESULT_BEGIN\n"
        '{"status":"first"}\n'
        "DOEFF_AGENT_RESULT_END\n"
        "DOEFF_AGENT_RESULT_BEGIN\n"
        "\x1b]0;Execute doeff-agents structured result workflow\x07"
        '{"status"\x1b[10G "broken"}\n'
        "DOEFF_AGENT_RESULT_END\n"
    )

    payload, error = _extract_result_payload(output)

    assert error is None
    assert payload == {"status": "first"}


def test_result_contract_prompt_requires_compact_single_line_json() -> None:
    prompt = _result_contract_prompt({"type": "object", "required": ["status"]})

    assert "compact single-line JSON object" in prompt
    assert "Do not pretty-print the result JSON" in prompt


def test_tmux_strip_ansi_removes_osc_and_csi_controls() -> None:
    text = "\x1b]0;title\x07DOEFF_AGENT_RESULT_BEGIN\x1b[?25l\n{}"

    assert strip_ansi(text) == "DOEFF_AGENT_RESULT_BEGIN\n{}"


def test_result_block_is_complete_only_after_end_marker() -> None:
    assert not _has_complete_result_block('DOEFF_AGENT_RESULT_BEGIN\n{"status": "ok"')
    assert _has_complete_result_block(
        'DOEFF_AGENT_RESULT_BEGIN\n{"status": "ok"}\nDOEFF_AGENT_RESULT_END'
    )


def test_result_validation_pattern_rejects_wrapped_identity_fields() -> None:
    schema = {
        "type": "object",
        "required": ["pr_url", "pr_head_sha", "branch"],
        "properties": {
            "pr_url": {
                "type": "string",
                "pattern": r"^https://github\.com/[^\s/]+/[^\s/]+/pull/[0-9]+$",
            },
            "pr_head_sha": {"type": "string", "pattern": r"^[0-9a-fA-F]{40}$"},
            "branch": {"type": "string", "pattern": r"^\S+$"},
        },
    }
    payload = {
        "pr_url": "https://github.com/example/repo/ pull/1",
        "pr_head_sha": "0123456789abcdef0123456789abcdef01234567",
        "branch": "feat/wrapped branch",
    }

    error = validate_result_payload(payload, schema)

    assert error is not None
    assert "result.pr_url" in error
    assert "pattern" in error


def test_tmux_backend_pastes_literal_prompt_and_resubmits_collapsed_input(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[1] == "-V":
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[1] == "capture-pane":
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=(
                    "✶ Spinning…\n"
                    "────────────────────────────────────────\n"
                    "\u276f\u00a0[Pasted text #3 +12 lines]\n"
                    "────────────────────────────────────────\n"
                    "  paste again to expand\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("doeff_agents.tmux.subprocess.run", fake_run)
    monkeypatch.setattr("doeff_agents.tmux.time.sleep", lambda _seconds: None)

    backend = TmuxSessionBackend()
    backend.send_keys("%42", "long structured-result prompt", literal=True, enter=True)

    command_names = [call[1] for call in calls]
    assert command_names.count("set-buffer") == 1
    assert command_names.count("paste-buffer") == 1
    assert command_names.count("delete-buffer") == 1
    assert command_names.count("capture-pane") == 3
    enter_calls = [
        call for call in calls if call[1:4] == ["send-keys", "-t", "%42"] and call[-1] == "Enter"
    ]
    assert len(enter_calls) == 4


def test_tmux_backend_rechecks_resubmitted_literal_prompt_until_clear(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []
    captures = [
        (
            "────────────────────────────────────────────────────────────────\n"
            "\u276f\u00a0\n"
            "────────────────────────────────────────────────────────────────\n"
            "\n"
            "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents\n"
            ". Continue autonomously if safe, or return a blocked/error structured result.\n"
        ),
        "\u276f\u00a0\n",
    ]

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[1] == "-V":
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[1] == "capture-pane":
            output = captures.pop(0) if captures else "\u276f\u00a0\n"
            return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("doeff_agents.tmux.subprocess.run", fake_run)
    monkeypatch.setattr("doeff_agents.tmux.time.sleep", lambda _seconds: None)

    sent_text = (
        "The kabuStation executor appeared to be waiting for input. "
        "Continue autonomously if safe, or return a blocked/error structured result."
    )
    backend = TmuxSessionBackend()
    backend.send_keys("%42", sent_text, literal=True, enter=True)

    command_names = [call[1] for call in calls]
    assert command_names.count("capture-pane") == 2
    enter_calls = [
        call for call in calls if call[1:4] == ["send-keys", "-t", "%42"] and call[-1] == "Enter"
    ]
    assert len(enter_calls) == 2


def test_unsubmitted_paste_detector_uses_latest_prompt_line() -> None:
    historical_paste = (
        "\u276f\u00a0[Pasted text #1 +2 lines]\n"
        "⏺ Write(notes.txt)\n"
        "  ⎿ Wrote 1 lines to notes.txt\n\n"
        "\u276f\u00a0\n"
    )

    assert not _output_has_unsubmitted_paste_input(historical_paste)
    assert _output_has_unsubmitted_paste_input("\u203a [Pasted text #1 +12 lines]\n")
    assert not _output_has_unsubmitted_paste_input("\u276f\u00a0\n")


def test_unsubmitted_paste_detector_catches_visible_prompt_text() -> None:
    output = (
        "────────────────────────────────────────────────────────────────\n"
        "\u276f\u00a0\n"
        "────────────────────────────────────────────────────────────────\n"
        "\n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents\n"
        ". Continue autonomously if safe, or return a blocked/error structured result.\n"
    )
    sent_text = (
        "The kabuStation executor appeared to be waiting for input. "
        "Continue autonomously if safe, or return a blocked/error structured result."
    )

    assert _output_has_unsubmitted_paste_input(output, sent_text)


def test_unsubmitted_paste_detector_ignores_prior_submitted_text() -> None:
    output = (
        "The kabuStation executor appeared to be waiting for input. "
        "Continue autonomously if safe, or return a blocked/error structured result.\n"
        "⏺ Running tools\n"
        "\u276f\u00a0\n"
    )
    sent_text = (
        "The kabuStation executor appeared to be waiting for input. "
        "Continue autonomously if safe, or return a blocked/error structured result."
    )

    assert not _output_has_unsubmitted_paste_input(output, sent_text)


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
