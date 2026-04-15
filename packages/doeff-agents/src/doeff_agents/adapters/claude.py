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

        Creates minimal config so Claude Code starts without interactive
        onboarding dialogs that block non-interactive (tmux) sessions.
        """
        home = Path.home()
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)

        # ~/.claude.json — Claude Code warns and may stall without it
        claude_json = home / ".claude.json"
        if not claude_json.exists():
            claude_json.write_text("{}")
            logger.info("Created %s", claude_json)

        # ~/.claude/config.json — must have hasCompletedOnboarding
        config_path = claude_dir / "config.json"
        if not config_path.exists():
            config_path.write_text(json.dumps({"hasCompletedOnboarding": True}))
            logger.info("Created %s", config_path)

        # ~/.claude/settings.json
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
