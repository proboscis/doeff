"""Adapter for OpenAI Codex CLI."""

import shutil
from pathlib import Path

import hy  # noqa: F401  # .hy import hook — the readiness physics home is a Hy module

from doeff_agents.sessionhost.impls.ready_physics import CODEX_READY_PATTERN

from .base import AgentType, InjectionMethod, LaunchParams


class CodexAdapter:
    """Adapter for OpenAI Codex CLI.

    Launch paths gate the first prompt paste on ``ready_pattern``; callers
    that go through the imperative ``session.py`` API must pre-trust the
    workspace (``trust_workspace_in_codex_home``) or the trust dialog keeps
    the composer hidden and the launch fails with AgentReadyTimeoutError.
    """

    @property
    def agent_type(self) -> AgentType:
        return AgentType.CODEX

    def is_available(self) -> bool:
        return shutil.which("codex") is not None

    def launch_command(self, params: LaunchParams) -> list[str]:
        """Return argv list - caller will shlex.join() if needed.

        The task prompt is never a CLI argument. Codex is launched as an
        interactive terminal session and the prompt is typed later through the
        terminal transport, keeping the process alive for validation retries.
        """
        args = ["codex", "--yolo"]

        if params.effort:
            args.extend(["-c", f"model_reasoning_effort={toml_quoted_string(params.effort)}"])

        for server_name, server_url in sorted((params.mcp_servers or {}).items()):
            args.extend([
                "-c",
                (
                    f"mcp_servers.{toml_quoted_key(server_name)}.url="
                    f"{toml_quoted_string(server_url)}"
                ),
            ])

        if params.model:
            args.extend(["--model", params.model])

        return args

    @property
    def injection_method(self) -> InjectionMethod:
        return InjectionMethod.TMUX

    @property
    def ready_pattern(self) -> str | None:
        # Physics home: sessionhost/impls/ready_physics.hy (ADR-DOE-AGENTS-008
        # R1) — the idle composer with menu/MCP-boot exclusions.
        return CODEX_READY_PATTERN

    @property
    def status_bar_lines(self) -> int:
        return 3  # Codex's status bar area


def toml_quoted_key(value: str) -> str:
    """Render a TOML quoted key segment."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def toml_quoted_string(value: str) -> str:
    """Render a TOML string literal for Codex -c key=value overrides."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def trust_workspace_in_codex_home(codex_home: str | Path, work_dir: str | Path) -> Path:
    """Persist Codex project trust for a workspace and return the config path."""
    config_path = Path(codex_home).expanduser() / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    workspace = str(Path(work_dir))
    header = f"[projects.{toml_quoted_key(workspace)}]"
    trust_line = 'trust_level = "trusted"'
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    lines = text.splitlines()

    for index, line in enumerate(lines):
        if line.strip() != header:
            continue
        end = index + 1
        while end < len(lines) and not lines[end].startswith("["):
            end += 1
        for trust_index in range(index + 1, end):
            if lines[trust_index].strip().startswith("trust_level"):
                lines[trust_index] = trust_line
                config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return config_path
        lines.insert(index + 1, trust_line)
        config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return config_path

    if lines and lines[-1] != "":
        lines.append("")
    lines.extend([header, trust_line])
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path
