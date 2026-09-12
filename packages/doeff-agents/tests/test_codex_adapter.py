"""Tests for Codex adapter command construction."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents.adapters.base import InjectionMethod, LaunchParams
from doeff_agents.adapters.codex import CodexAdapter, trust_workspace_in_codex_home

from fake_io_support import FakeIoWorld, fake_io_root


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


def test_trust_workspace_persists_project_trust(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    work_dir = tmp_path / 'hypha "quoted" workspace'
    world = FakeIoWorld()

    config_path = fake_io_root(world)(trust_workspace_in_codex_home(codex_home, work_dir))
    escaped_work_dir = str(work_dir).replace('"', '\\"')

    assert config_path == codex_home / "config.toml"
    assert world.files[str(config_path)] == (
        f'[projects."{escaped_work_dir}"]\n'
        'trust_level = "trusted"\n'
    )


def test_trust_workspace_updates_existing_project_table(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    work_dir = tmp_path / "workspace"
    config_path = codex_home / "config.toml"
    world = FakeIoWorld(
        files={
            str(config_path): (
                f'[projects."{work_dir}"]\n'
                'foo = "bar"\n'
                'trust_level = "untrusted"\n'
                "\n[notice]\n"
                "hide_full_access_warning = true\n"
            )
        }
    )

    fake_io_root(world)(trust_workspace_in_codex_home(codex_home, work_dir))

    assert world.files[str(config_path)] == (
        f'[projects."{work_dir}"]\n'
        'foo = "bar"\n'
        'trust_level = "trusted"\n'
        "\n[notice]\n"
        "hide_full_access_warning = true\n"
    )
