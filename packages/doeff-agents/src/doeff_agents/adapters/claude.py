"""Adapter for Claude Code CLI."""

import json
import logging
import os
import shutil
from pathlib import Path

from .base import AgentType, InjectionMethod, LaunchParams

logger = logging.getLogger("doeff_agents.claude")


class ClaudeAdapter:
    """Adapter for Claude Code CLI."""

    @property
    def agent_type(self) -> AgentType:
        return AgentType.CLAUDE

    def is_available(self) -> bool:
        return shutil.which("claude") is not None

    def pre_launch(self) -> None:
        """Verify Claude Code is installed and authenticated before launch.

        Requires ~/.claude.json with oauthAccount to exist — this file is
        created by running `claude` interactively and completing login.
        doeff-agents will NOT create it automatically because authentication
        is the user's responsibility.

        On local machines: run `claude` once to authenticate.
        On k3s: mount ~/.claude.json via PVC backup or k8s secret.
        """
        home = Path.home()
        claude_json = home / ".claude.json"

        # If CLAUDE_CODE_OAUTH_TOKEN is set, Claude Code will authenticate
        # via env var — no need for oauthAccount in .claude.json.
        # Still need .claude.json to exist (Claude Code expects it).
        has_oauth_env = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))

        if not claude_json.exists():
            if has_oauth_env:
                # Create minimal .claude.json — Claude Code will populate
                # oauthAccount after authenticating via env var.
                claude_json.write_text("{}")
                logger.info("Created minimal %s (CLAUDE_CODE_OAUTH_TOKEN set)", claude_json)
            else:
                raise RuntimeError(
                    f"{claude_json} not found.\n"
                    "\n"
                    "Claude Code must be installed and authenticated before "
                    "doeff-agents can launch a session.\n"
                    "\n"
                    "To fix:\n"
                    "  Local:  run `claude` in a terminal and complete login\n"
                    "  k3s:    set CLAUDE_CODE_OAUTH_TOKEN env var from k8s secret,\n"
                    "          or mount ~/.claude.json via PVC backup\n"
                    "  Docker: COPY a pre-authenticated .claude.json into the image\n"
                )

        # Verify oauthAccount is present (unless env var handles auth)
        if not has_oauth_env:
            try:
                data = json.loads(claude_json.read_text())
            except (json.JSONDecodeError, OSError):
                data = {}
            if "oauthAccount" not in data:
                raise RuntimeError(
                    f"{claude_json} exists but has no oauthAccount.\n"
                    "\n"
                    "Claude Code is not authenticated. Run `claude` in a terminal "
                    "and complete login, then try again.\n"
                )

        # Ensure supporting config files exist (these are safe to create)
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)

        config_path = claude_dir / "config.json"
        if not config_path.exists():
            config_path.write_text(json.dumps({"hasCompletedOnboarding": True}))
            logger.info("Created %s", config_path)

        settings_path = claude_dir / "settings.json"
        if not settings_path.exists():
            settings_path.write_text("{}")

    def launch_command(self, params: LaunchParams) -> list[str]:
        """Return argv list - caller will shlex.join() if needed."""
        args = ["claude", "--dangerously-skip-permissions"]

        if params.model:
            args.extend(["--model", params.model])

        # Prompt is passed as positional argument (no quoting needed in argv)
        if params.prompt:
            args.append(params.prompt)

        return args

    @property
    def injection_method(self) -> InjectionMethod:
        return InjectionMethod.ARG

    @property
    def ready_pattern(self) -> str | None:
        return None  # Prompt passed via command line

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
