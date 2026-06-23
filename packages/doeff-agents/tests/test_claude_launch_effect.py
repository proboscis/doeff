"""Tests for Claude-specific launch lowering and workspace bootstrap."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents import (
    ClaudeLaunchEffect,
    ClaudeRuntimePolicy,
    SessionConfig,
    TmuxAgentHandler,
)
from doeff_agents.adapters.base import AgentType, InjectionMethod, LaunchParams
from doeff_agents.session_backend import SessionBackend


class FakeClaudeAdapter:
    agent_type = AgentType.CLAUDE
    injection_method = InjectionMethod.TMUX
    ready_pattern = None
    status_bar_lines = 0

    def launch_command(self, cfg: LaunchParams) -> list[str]:
        args = ["fake-claude"]
        if cfg.model:
            args.extend(["--model", cfg.model])
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

    def send_keys(
        self, target: str, keys: str, *, literal: bool = True, enter: bool = True
    ) -> None:
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
    monkeypatch.setattr(production_mod, "_dismiss_onboarding_dialogs", lambda *_args, **_kwargs: 0)

    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    agent_home = tmp_path / "agent-home"
    handler = TmuxAgentHandler(
        backend=backend,
        claude_runtime_policy=ClaudeRuntimePolicy(
            agent_home=agent_home,
            trusted_workspaces=(work_dir,),
            bootstrap_exports={"FOO": "bar"},
        ),
    )
    effect = ClaudeLaunchEffect(
        session_name="worker",
        work_dir=work_dir,
        prompt="Write result.json",
        model="opus",
    )

    handle = handler.handle_claude_launch(effect)

    assert handle.session_id == "worker"
    assert not hasattr(handle, "agent_type")
    assert backend.created[0].work_dir == work_dir
    sent = backend.sent[0][1]
    assert "HOME=" in sent
    assert str(agent_home) in sent
    assert "FOO=bar" in sent
    assert "fake-claude --model opus" in sent
    assert "Write result.json" not in sent
    assert backend.sent[1] == ("%worker", "Write result.json", True, True)


def test_handle_claude_launch_rejects_anthropic_api_key_session_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from doeff_agents.handlers import production as production_mod

    backend = FakeBackend()
    monkeypatch.setattr(production_mod, "get_adapter", lambda _agent_type: FakeClaudeAdapter())

    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    forbidden_key = "ANTHROPIC" + "_API_KEY"
    handler = TmuxAgentHandler(backend=backend)
    effect = ClaudeLaunchEffect(
        session_name="worker",
        work_dir=work_dir,
        prompt="Write result.json",
        session_env={forbidden_key: "secret"},
    )

    with pytest.raises(ValueError, match="Anthropic API keys"):
        handler.handle_claude_launch(effect)

    assert backend.created == []


def test_handle_claude_launch_rejects_anthropic_api_key_bootstrap_exports(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from doeff_agents.handlers import production as production_mod

    backend = FakeBackend()
    monkeypatch.setattr(production_mod, "get_adapter", lambda _agent_type: FakeClaudeAdapter())

    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    forbidden_key = "ANTHROPIC" + "_API_KEY"
    handler = TmuxAgentHandler(
        backend=backend,
        claude_runtime_policy=ClaudeRuntimePolicy(
            bootstrap_exports={forbidden_key: "secret"},
        ),
    )
    effect = ClaudeLaunchEffect(
        session_name="worker",
        work_dir=work_dir,
        prompt="Write result.json",
    )

    with pytest.raises(ValueError, match="Anthropic API keys"):
        handler.handle_claude_launch(effect)

    assert backend.created == []
