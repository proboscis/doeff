"""Tests for Claude-specific launch lowering and workspace bootstrap."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents import (  # noqa: E402
    AgentTaskSpec,
    ClaudeLaunchEffect,
    ClaudeRuntimePolicy,
    ExpectedArtifact,
    LaunchTask,
    SessionConfig,
    SessionHandle,
    TmuxAgentHandler,
    WorkspaceFile,
    lower_task_launch_to_claude,
)
from doeff_agents.adapters.base import AgentType, InjectionMethod, LaunchConfig  # noqa: E402
from doeff_agents.session_backend import SessionBackend  # noqa: E402


class FakeClaudeAdapter:
    agent_type = AgentType.CLAUDE
    injection_method = InjectionMethod.ARG
    ready_pattern = None
    status_bar_lines = 0

    def launch_command(self, cfg: LaunchConfig) -> list[str]:
        args = ["fake-claude"]
        if cfg.model:
            args.extend(["--model", cfg.model])
        if cfg.prompt:
            args.append(cfg.prompt)
        return args

    def is_available(self) -> bool:
        return True


class FakeBackend(SessionBackend):
    def __init__(self) -> None:
        self.sessions: set[str] = set()
        self.created: list[SessionConfig] = []
        self.sent: list[tuple[str, str, bool, bool]] = []

    def is_available(self) -> bool:
        return True

    def is_inside_session(self) -> bool:
        return False

    def has_session(self, name: str) -> bool:
        return name in self.sessions

    def new_session(self, cfg: SessionConfig):
        self.created.append(cfg)
        self.sessions.add(cfg.session_name)
        return type(
            "SessionInfo",
            (),
            {
                "session_name": cfg.session_name,
                "pane_id": f"%{cfg.session_name}",
                "created_at": datetime.now(timezone.utc),
            },
        )()

    def send_keys(self, target: str, keys: str, *, literal: bool = True, enter: bool = True) -> None:
        self.sent.append((target, keys, literal, enter))

    def capture_pane(self, target: str, lines: int = 100, *, strip_ansi_codes: bool = True) -> str:
        return ""

    def kill_session(self, session: str) -> None:
        self.sessions.discard(session)

    def attach_session(self, session: str) -> None:
        raise NotImplementedError

    def list_sessions(self) -> list[str]:
        return sorted(self.sessions)


def test_prepare_claude_home_seeds_trusted_workspace(tmp_path: Path) -> None:
    backend = FakeBackend()
    handler = TmuxAgentHandler(backend=backend)

    agent_home = tmp_path / "agent-home"
    workspace = tmp_path / "workspace"
    handler._prepare_claude_home(agent_home, (workspace,))

    claude_json = json.loads((agent_home / ".claude.json").read_text())
    project = claude_json["projects"][str(workspace)]
    assert project["hasTrustDialogAccepted"] is True
    assert project["hasCompletedProjectOnboarding"] is True

    config = json.loads((agent_home / ".claude" / "config.json").read_text())
    assert config["hasCompletedOnboarding"] is True
    assert (agent_home / ".claude" / "settings.json").exists()


def test_handle_claude_launch_materializes_workspace_and_uses_runtime_env(
    monkeypatch, tmp_path: Path
) -> None:
    from doeff_agents.handlers import production as production_mod

    backend = FakeBackend()
    monkeypatch.setattr(production_mod, "get_adapter", lambda _agent_type: FakeClaudeAdapter())
    monkeypatch.setattr(production_mod, "_dismiss_onboarding_dialogs", lambda *a, **k: 0)

    handler = TmuxAgentHandler(backend=backend)
    work_dir = tmp_path / "workspace"
    agent_home = tmp_path / "agent-home"
    effect = ClaudeLaunchEffect(
        session_name="worker",
        task=AgentTaskSpec(
            work_dir=work_dir,
            instructions="Write result.json",
            workspace_files=(
                WorkspaceFile(relative_path=Path("CLAUDE.md"), content="hello"),
                WorkspaceFile(relative_path=Path("result.json"), content="{}", executable=False),
            ),
            expected_artifacts=(ExpectedArtifact(relative_path=Path("result.json")),),
        ),
        model="opus",
        agent_home=agent_home,
        trusted_workspaces=(work_dir,),
        bootstrap_exports={"FOO": "bar"},
    )

    handle = handler.handle_claude_launch(effect)

    assert handle.session_name == "worker"
    assert handle.agent_type == AgentType.CLAUDE
    assert (work_dir / "CLAUDE.md").read_text() == "hello"
    assert backend.created[0].work_dir == work_dir
    sent = backend.sent[0][1]
    assert "HOME=" in sent
    assert str(agent_home) in sent
    assert "FOO=bar" in sent
    assert "fake-claude --model opus 'Write result.json'" in sent


def test_handle_launch_task_lowers_to_claude(monkeypatch, tmp_path: Path) -> None:
    from doeff_agents.handlers import production as production_mod

    backend = FakeBackend()
    monkeypatch.setattr(production_mod, "get_adapter", lambda _agent_type: FakeClaudeAdapter())
    monkeypatch.setattr(production_mod, "_dismiss_onboarding_dialogs", lambda *a, **k: 0)

    policy = ClaudeRuntimePolicy(
        model="opus",
        agent_home=tmp_path / "agent-home",
        bootstrap_exports={"BAR": "baz"},
    )
    handler = TmuxAgentHandler(backend=backend, claude_runtime_policy=policy)

    effect = LaunchTask(
        "worker",
        AgentTaskSpec(
            work_dir=tmp_path / "workspace",
            instructions="Do the thing",
        ),
        tags=("safe", "paper"),
        ready_timeout_sec=12.0,
    )

    handle = handler.handle_launch_task(effect)

    assert isinstance(handle, SessionHandle)
    assert handle.agent_type == AgentType.CLAUDE
    assert backend.created[0].work_dir == tmp_path / "workspace"
    assert "BAR=baz" in backend.sent[0][1]
