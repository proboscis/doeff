"""Tests for Codex adapter command construction."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents.adapters.base import LaunchParams
from doeff_agents.adapters.codex import CodexAdapter


def test_launch_command_includes_model_when_provided() -> None:
    adapter = CodexAdapter()
    params = LaunchParams(
        work_dir=Path.cwd(),
        prompt="ship it",
        model="gpt-5.5",
    )

    assert adapter.launch_command(params) == [
        "codex",
        "--full-auto",
        "--model",
        "gpt-5.5",
        "ship it",
    ]


def test_launch_command_omits_model_when_not_provided() -> None:
    adapter = CodexAdapter()
    params = LaunchParams(
        work_dir=Path.cwd(),
        prompt="ship it",
    )

    assert adapter.launch_command(params) == [
        "codex",
        "--full-auto",
        "ship it",
    ]
