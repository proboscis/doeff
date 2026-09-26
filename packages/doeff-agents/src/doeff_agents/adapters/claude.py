"""Adapter for Claude Code CLI."""

import json
import logging
import posixpath

import hy  # noqa: F401  # .hy import hook — the readiness physics home is a Hy module
from doeff import Program, do

from doeff_agents.io_effects import (
    home_path,
    make_dirs,
    path_exists,
    read_text,
    write_text,
)
from doeff_agents.io_root import IoGenerator, as_bool, as_optional_str, as_str
from doeff_agents.ready_physics import CLAUDE_SCREEN_READER_READY_PATTERN

from .base import AgentType, InjectionMethod, LaunchParams, cli_available

MISSING_CLAUDE_JSON = (
    "{path} not found.\n"
    "\n"
    "Claude Code must be installed and authenticated before "
    "doeff-agents can launch a session.\n"
    "\n"
    "To fix:\n"
    "  Local:  run `claude` in a terminal and complete login\n"
    "  k3s:    mount a pre-authenticated Claude profile via PVC\n"
    "  Docker: COPY a pre-authenticated .claude.json into the image\n"
)
UNAUTHENTICATED_CLAUDE_JSON = (
    "{path} exists but has no oauthAccount.\n"
    "\n"
    "Claude Code is not authenticated. Run `claude` in a terminal "
    "and complete login, then try again.\n"
)


def authenticated_account(raw_text: str | None) -> bool:
    """Pure judgment: does this ``.claude.json`` text carry an oauthAccount?"""
    if raw_text is None:
        return False
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and "oauthAccount" in data

logger = logging.getLogger("doeff_agents.claude")


class ClaudeAdapter:
    """Adapter for Claude Code CLI."""

    @property
    def agent_type(self) -> AgentType:
        return AgentType.CLAUDE

    def available(self) -> Program:
        """Program answering whether the Claude Code CLI is on PATH."""
        return cli_available("claude")

    def pre_launch(self) -> Program:
        """Program verifying Claude Code is installed and authenticated."""
        return claude_pre_launch()

    def launch_command(self, params: LaunchParams) -> list[str]:
        """Return argv list - caller will shlex.join() if needed.

        Claude must be launched as an interactive terminal session. The prompt
        is injected later through the terminal transport so the process stays
        alive for validation retries and never enters single-shot print mode.
        """
        args = [
            "claude",
            "--ax-screen-reader",
            "--dangerously-skip-permissions",
            "--permission-mode",
            "bypassPermissions",
        ]

        if params.model:
            args.extend(["--model", params.model])

        if params.effort:
            args.extend(["--effort", params.effort])

        if params.bare:
            args.append("--bare")

        if params.mcp_servers:
            mcp_config = {
                "mcpServers": {
                    name: {"type": "sse", "url": url}
                    for name, url in sorted(params.mcp_servers.items())
                },
            }
            args.extend(["--mcp-config", json.dumps(mcp_config, sort_keys=True)])
            args.append("--strict-mcp-config")

        return args

    @property
    def injection_method(self) -> InjectionMethod:
        return InjectionMethod.TMUX

    @property
    def ready_pattern(self) -> str | None:
        # Physics home: doeff_agents/ready_physics.hy (ADR-DOE-AGENTS-008
        # R1) — readiness in screen-reader mode is the permission-mode footer.
        return CLAUDE_SCREEN_READER_READY_PATTERN

    @property
    def trust_dialog_pattern(self) -> str | None:
        return r"Yes, I trust this folder"

    @property
    def onboarding_patterns(self) -> list[str]:
        """Patterns for onboarding dialogs that need Enter to dismiss."""
        return [
            r"Choose the text style",          # Theme selection
            r"Select login method",            # Auth method selection
            r"Press Enter to continue",        # Security notes / login success
            r"Paste code here",               # OAuth code paste prompt
            r"Yes, I trust this folder",        # Trust dialog
        ]

    @property
    def bypass_permissions_pattern(self) -> str:
        """Pattern for bypass permissions confirmation (need Down+Enter)."""
        return r"Yes, I accept"

    @property
    def status_bar_lines(self) -> int:
        return 5  # Claude's status bar area


@do
def claude_pre_launch() -> IoGenerator[None]:
    """Program verifying Claude Code is installed and authenticated.

    Requires ~/.claude.json with oauthAccount to exist — this file is
    created by running `claude` interactively and completing login.
    doeff-agents will NOT create it automatically because authentication
    is the user's responsibility.

    On local machines: run `claude` once to authenticate.
    On k3s: mount a pre-authenticated Claude profile via PVC or secret.

    段 7 lane 7c(決定 1.3): 判断(認証済みか・何を作るか)はここ、
    file の読み書きは `doeff_agents.io_effects` の要求で handler が持つ。
    """
    home = as_str((yield home_path()))
    claude_json = posixpath.join(home, ".claude.json")

    present = as_bool((yield path_exists(claude_json)))
    if not present:
        raise RuntimeError(MISSING_CLAUDE_JSON.format(path=claude_json))

    raw_text = as_optional_str((yield read_text(claude_json)))
    if not authenticated_account(raw_text):
        raise RuntimeError(UNAUTHENTICATED_CLAUDE_JSON.format(path=claude_json))

    # Ensure supporting config files exist (these are safe to create)
    claude_dir = posixpath.join(home, ".claude")
    yield make_dirs(claude_dir)

    config_path = posixpath.join(claude_dir, "config.json")
    config_present = as_bool((yield path_exists(config_path)))
    if not config_present:
        yield write_text(config_path, json.dumps({"hasCompletedOnboarding": True}))
        logger.info("Created %s", config_path)

    settings_path = posixpath.join(claude_dir, "settings.json")
    settings_present = as_bool((yield path_exists(settings_path)))
    if not settings_present:
        yield write_text(settings_path, "{}")
    return None
