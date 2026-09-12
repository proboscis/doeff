"""Tests for Claude adapter command construction."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents.adapters.base import InjectionMethod, LaunchParams
from doeff_agents.adapters.claude import ClaudeAdapter

from fake_io_support import FakeIoWorld, fake_io_root


def test_claude_adapter_uses_tmux_prompt_injection() -> None:
    assert ClaudeAdapter().injection_method == InjectionMethod.TMUX


def test_launch_command_includes_model_when_provided() -> None:
    adapter = ClaudeAdapter()
    params = LaunchParams(
        work_dir=Path.cwd(),
        prompt="ship it",
        model="opus",
    )

    assert adapter.launch_command(params) == [
        "claude",
        "--ax-screen-reader",
        "--dangerously-skip-permissions",
        "--permission-mode",
        "bypassPermissions",
        "--model",
        "opus",
    ]
    assert "ship it" not in adapter.launch_command(params)


def test_launch_command_omits_model_when_not_provided() -> None:
    adapter = ClaudeAdapter()
    params = LaunchParams(
        work_dir=Path.cwd(),
        prompt="ship it",
    )

    assert adapter.launch_command(params) == [
        "claude",
        "--ax-screen-reader",
        "--dangerously-skip-permissions",
        "--permission-mode",
        "bypassPermissions",
    ]
    assert "ship it" not in adapter.launch_command(params)


def test_launch_command_without_prompt_stays_interactive() -> None:
    adapter = ClaudeAdapter()
    params = LaunchParams(
        work_dir=Path.cwd(),
        prompt=None,
        model="opus",
    )

    assert adapter.launch_command(params) == [
        "claude",
        "--ax-screen-reader",
        "--dangerously-skip-permissions",
        "--permission-mode",
        "bypassPermissions",
        "--model",
        "opus",
    ]


def test_launch_command_includes_mcp_config_when_servers_provided() -> None:
    adapter = ClaudeAdapter()
    params = LaunchParams(
        work_dir=Path.cwd(),
        prompt="ship it",
        model="opus",
        mcp_servers={"nak": "http://127.0.0.1:42175/sse"},
    )

    command = adapter.launch_command(params)

    assert command[:5] == [
        "claude",
        "--ax-screen-reader",
        "--dangerously-skip-permissions",
        "--permission-mode",
        "bypassPermissions",
    ]
    assert command[5:7] == [
        "--model",
        "opus",
    ]
    assert "--mcp-config" in command
    config_index = command.index("--mcp-config") + 1
    assert json.loads(command[config_index]) == {
        "mcpServers": {
            "nak": {
                "type": "sse",
                "url": "http://127.0.0.1:42175/sse",
            },
        },
    }
    assert "--strict-mcp-config" in command
    assert "ship it" not in command


def test_pre_launch_requires_an_authenticated_claude_json() -> None:
    """認証されていない home では pre-launch が typed に落ちる(黙って進まない)。"""
    world = FakeIoWorld(home="/home/agent")

    with pytest.raises(RuntimeError, match="not found"):
        fake_io_root(world)(ClaudeAdapter().pre_launch())

    world.files["/home/agent/.claude.json"] = '{"projects": {}}'
    with pytest.raises(RuntimeError, match="no oauthAccount"):
        fake_io_root(world)(ClaudeAdapter().pre_launch())


def test_pre_launch_creates_supporting_config_from_authenticated_home() -> None:
    world = FakeIoWorld(
        home="/home/agent",
        files={"/home/agent/.claude.json": '{"oauthAccount": {"name": "\u65e5\u672c\u8a9e"}}'},
    )

    fake_io_root(world)(ClaudeAdapter().pre_launch())

    assert world.files["/home/agent/.claude/config.json"] == '{"hasCompletedOnboarding": true}'
    assert world.files["/home/agent/.claude/settings.json"] == "{}"
    # 認証の file は書き換えない(認証は利用者の持ち物)。
    assert world.files["/home/agent/.claude.json"] == (
        '{"oauthAccount": {"name": "\u65e5\u672c\u8a9e"}}'
    )
