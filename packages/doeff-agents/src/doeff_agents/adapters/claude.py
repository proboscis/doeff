"""Adapter for Claude Code CLI."""

import json
import logging
import shutil
from pathlib import Path

from .base import AgentType, InjectionMethod, LaunchConfig

logger = logging.getLogger("doeff_agents.claude")


class ClaudeAdapter:
    """Adapter for Claude Code CLI."""

    @property
    def agent_type(self) -> AgentType:
        return AgentType.CLAUDE

    def is_available(self) -> bool:
        return shutil.which("claude") is not None

    def pre_launch(self) -> None:
        """Ensure Claude Code config files exist before launch.

        Creates config.json with hasCompletedOnboarding=true and settings.json
        if missing. Without these, Claude Code shows onboarding dialogs that
        block non-interactive (tmux) sessions.

        NOTE: .claude.json restoration from backups is NOT handled here.
        That is deployment-specific (e.g. k3s PVC) and belongs in the caller's
        infrastructure layer, not in a generic library.
        """
        home = Path.home()
        claude_dir = home / ".claude"

        # Ensure config.json exists with onboarding complete
        claude_dir.mkdir(parents=True, exist_ok=True)
        config_path = claude_dir / "config.json"
        if not config_path.exists():
            config_path.write_text(json.dumps({"hasCompletedOnboarding": True}))
            logger.info("Created %s with hasCompletedOnboarding=true", config_path)

        # Ensure settings.json exists
        settings_path = claude_dir / "settings.json"
        if not settings_path.exists():
            settings_path.write_text("{}")

    def launch_command(self, cfg: LaunchConfig) -> list[str]:
        """Return argv list - caller will shlex.join() if needed."""
        args = ["claude", "--dangerously-skip-permissions"]

        if cfg.model:
            args.extend(["--model", cfg.model])

        if cfg.profile:
            args.extend(["--profile", cfg.profile])

        if cfg.resume and cfg.session_name:
            args.extend(["--resume", cfg.session_name])

        # Prompt is passed as positional argument (no quoting needed in argv)
        if cfg.prompt:
            args.append(cfg.prompt)

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
