"""Tests for Claude adapter command construction."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents.adapters.base import LaunchParams
from doeff_agents.adapters.claude import ClaudeAdapter


def test_launch_command_includes_model_when_provided() -> None:
    adapter = ClaudeAdapter()
    params = LaunchParams(
        work_dir=Path.cwd(),
        prompt="ship it",
        model="opus",
    )

    assert adapter.launch_command(params) == [
        "claude",
        "--dangerously-skip-permissions",
        "--print",
        "--model",
        "opus",
        "ship it",
    ]


def test_launch_command_omits_model_when_not_provided() -> None:
    adapter = ClaudeAdapter()
    params = LaunchParams(
        work_dir=Path.cwd(),
        prompt="ship it",
    )

    assert adapter.launch_command(params) == [
        "claude",
        "--dangerously-skip-permissions",
        "--print",
        "ship it",
    ]


def test_launch_command_without_prompt_stays_interactive() -> None:
    adapter = ClaudeAdapter()
    params = LaunchParams(
        work_dir=Path.cwd(),
        prompt=None,
        model="opus",
    )

    assert adapter.launch_command(params) == [
        "claude",
        "--dangerously-skip-permissions",
        "--model",
        "opus",
    ]


def test_pre_launch_reads_and_writes_claude_files_as_utf8(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text('{"oauthAccount": {"name": "日本語"}}', encoding="utf-8")

    original_read_text = Path.read_text
    original_write_text = Path.write_text

    def read_text(path: Path, *args, **kwargs):
        if path.name == ".claude.json":
            assert kwargs.get("encoding") == "utf-8"
        return original_read_text(path, *args, **kwargs)

    def write_text(path: Path, data: str, *args, **kwargs):
        if path.name in {"config.json", "settings.json"}:
            assert kwargs.get("encoding") == "utf-8"
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "write_text", write_text)

    ClaudeAdapter().pre_launch()
