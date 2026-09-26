"""Adapter for OpenAI Codex CLI."""

import posixpath
from pathlib import Path

import hy  # noqa: F401  # .hy import hook — the readiness physics home is a Hy module
from doeff import Program, do

from doeff_agents.io_effects import make_dirs, read_text, write_text
from doeff_agents.io_root import IoGenerator, as_optional_str
from doeff_agents.ready_physics import CODEX_READY_PATTERN

from .base import AgentType, InjectionMethod, LaunchParams, cli_available


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

    def available(self) -> Program:
        """Program answering whether the Codex CLI is on PATH."""
        return cli_available("codex")

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
        # Physics home: doeff_agents/ready_physics.hy (ADR-DOE-AGENTS-008
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


def codex_config_path(codex_home: str | Path) -> str:
    """Pure judgment: where Codex keeps the trust ledger for this home."""
    return posixpath.join(str(Path(codex_home).expanduser()), "config.toml")


def trusted_config_text(text: str, work_dir: str | Path) -> str:
    """Pure judgment: ``config.toml`` text with this workspace marked trusted.

    段 7 lane 7c(決定 1.3): 編集の判断はここ(入力 text → 出力 text)で、
    file の読み書きは呼び手の program が要求する。
    """
    workspace = str(Path(work_dir))
    header = f"[projects.{toml_quoted_key(workspace)}]"
    trust_line = 'trust_level = "trusted"'
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
                return "\n".join(lines) + "\n"
        lines.insert(index + 1, trust_line)
        return "\n".join(lines) + "\n"

    if lines and lines[-1] != "":
        lines.append("")
    lines.extend([header, trust_line])
    return "\n".join(lines) + "\n"


@do
def trust_workspace_in_codex_home(codex_home: str | Path, work_dir: str | Path) -> IoGenerator[Path]:
    """Program persisting Codex project trust; returns the config path."""
    config_path = codex_config_path(codex_home)
    yield make_dirs(posixpath.dirname(config_path))
    text = as_optional_str((yield read_text(config_path)))
    yield write_text(config_path, trusted_config_text(text or "", work_dir))
    return Path(config_path)
