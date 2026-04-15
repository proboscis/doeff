"""Tests for Claude adapter command construction."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents.adapters.base import AgentType, LaunchConfig
from doeff_agents.adapters.claude import ClaudeAdapter


def test_launch_command_includes_model_when_provided() -> None:
    adapter = ClaudeAdapter()
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="ship it",
        model="opus",
    )

    assert adapter.launch_command(config) == [
        "claude",
        "--dangerously-skip-permissions",
        "--model",
        "opus",
        "ship it",
    ]


def test_launch_command_omits_model_when_not_provided() -> None:
    adapter = ClaudeAdapter()
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="ship it",
    )

    assert adapter.launch_command(config) == [
        "claude",
        "--dangerously-skip-permissions",
        "ship it",
    ]
