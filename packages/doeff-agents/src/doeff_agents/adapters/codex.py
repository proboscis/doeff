"""Adapter for OpenAI Codex CLI."""

import shutil
from pathlib import Path

from .base import AgentType, InjectionMethod, LaunchParams

# Readiness = the idle composer is on screen (a line starting with the
# U+203A prompt marker plus text that is not a numbered menu option) and no
# MCP-boot status line is visible. Derived from verbatim codex 0.144.4
# captures (tests/data/ready_screens/) and the doeff-agentd oracle physics:
#
# - login screen (no auth in CODEX_HOME) renders its menu with ASCII ">",
#   never U+203A — it must time out, not match;
# - trust and update dialogs draw "<U+203A> <digit>. <option>" selection
#   markers, excluded by the (?!\d+\.[ \t]) lookahead (Enter on the update
#   dialog's default option starts a global npm upgrade);
# - the composer is already drawn while "Starting MCP servers (N/M)" is
#   still on screen, but the input loop is not wired yet — keys sent in
#   that window leave the prompt sitting unsubmitted (main.rs
#   wait_for_repl_idle), hence the \A(?!...) exclusion; codex replaces
#   that status line once MCP startup settles.
CODEX_READY_PATTERN = r"(?ims)\A(?!.*starting mcp servers).*^\u203a[ \t]+(?!\d+\.[ \t])\S"


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
