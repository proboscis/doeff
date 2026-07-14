"""Tests for Codex adapter command construction."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents.adapters.base import InjectionMethod, LaunchParams
from doeff_agents.adapters.codex import CodexAdapter, trust_workspace_in_codex_home


def test_launch_command_includes_model_when_provided() -> None:
    adapter = CodexAdapter()
    params = LaunchParams(
        work_dir=Path.cwd(),
        prompt="ship it",
        model="gpt-5.5",
        effort="xhigh",
        mcp_servers={"hypha": "http://127.0.0.1:51978/sse"},
    )

    assert adapter.launch_command(params) == [
        "codex",
        "--yolo",
        "-c",
        'model_reasoning_effort="xhigh"',
        "-c",
        'mcp_servers."hypha".url="http://127.0.0.1:51978/sse"',
        "--model",
        "gpt-5.5",
    ]
    assert "ship it" not in adapter.launch_command(params)


def test_launch_command_omits_model_when_not_provided() -> None:
    adapter = CodexAdapter()
    params = LaunchParams(
        work_dir=Path.cwd(),
        prompt="ship it",
    )

    assert adapter.launch_command(params) == [
        "codex",
        "--yolo",
    ]
    assert "ship it" not in adapter.launch_command(params)


def test_launch_command_never_uses_removed_full_auto_flag() -> None:
    adapter = CodexAdapter()
    params = LaunchParams(
        work_dir=Path.cwd(),
        prompt="ship it",
        model="gpt-5.5",
        effort="xhigh",
    )

    assert "--full-auto" not in adapter.launch_command(params)


def test_launch_command_quotes_mcp_server_config() -> None:
    adapter = CodexAdapter()
    params = LaunchParams(
        work_dir=Path.cwd(),
        prompt="ship it",
        mcp_servers={'hypha "local"': r"http://127.0.0.1:51978/a\b/sse"},
    )

    assert adapter.launch_command(params) == [
        "codex",
        "--yolo",
        "-c",
        'mcp_servers."hypha \\"local\\"".url="http://127.0.0.1:51978/a\\\\b/sse"',
    ]


def test_codex_adapter_uses_tmux_prompt_injection() -> None:
    assert CodexAdapter().injection_method == InjectionMethod.TMUX


def test_codex_ready_pattern_matches_input_prompt_but_not_startup_dialogs() -> None:
    pattern = CodexAdapter().ready_pattern
    login_screen = "Welcome to Codex\n\nPress enter to continue\n"
    update_dialog = (
        "Update available!\n"
        "\u203a 1. Update now (runs `npm install -g @openai/codex`)\n"
        "  2. Skip\n"
        "  3. Skip until next version\n"
        "Press enter to continue\n"
    )
    ready_screen = (
        "OpenAI Codex\n\n"
        "\u203a Ask Codex to do anything\n"
        "  gpt-5.5 default \u00b7 /workspace\n"
    )

    assert pattern is not None
    assert re.search(pattern, login_screen) is None
    assert re.search(pattern, update_dialog) is None
    assert re.search(pattern, ready_screen) is not None


def test_trust_workspace_persists_project_trust(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    work_dir = tmp_path / 'hypha "quoted" workspace'

    config_path = trust_workspace_in_codex_home(codex_home, work_dir)
    escaped_work_dir = str(work_dir).replace('"', '\\"')

    assert config_path == codex_home / "config.toml"
    assert config_path.read_text(encoding="utf-8") == (
        f'[projects."{escaped_work_dir}"]\n'
        'trust_level = "trusted"\n'
    )


def test_trust_workspace_updates_existing_project_table(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    work_dir = tmp_path / "workspace"
    config_path = codex_home / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        f'[projects."{work_dir}"]\n'
        'foo = "bar"\n'
        'trust_level = "untrusted"\n'
        "\n[notice]\n"
        "hide_full_access_warning = true\n",
        encoding="utf-8",
    )

    trust_workspace_in_codex_home(codex_home, work_dir)

    assert config_path.read_text(encoding="utf-8") == (
        f'[projects."{work_dir}"]\n'
        'foo = "bar"\n'
        'trust_level = "trusted"\n'
        "\n[notice]\n"
        "hide_full_access_warning = true\n"
    )
