"""Claude Code home preparation shared by agent handlers."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


def prepare_claude_home(agent_home: Path, trusted_workspaces: tuple[Path, ...]) -> None:
    """Prepare isolated Claude Code state so launches do not block on dialogs."""
    source_home = Path.home()
    claude_dir = agent_home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    candidate_json_paths: list[Path] = [
        agent_home / ".claude.json",
        agent_home / ".claude" / ".claude.json",
    ]
    source_claude_json = source_home / ".claude.json"

    for claude_json in candidate_json_paths:
        if agent_home != source_home and not claude_json.exists() and source_claude_json.exists():
            claude_json.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_claude_json, claude_json)

    for claude_json in candidate_json_paths:
        data = json.loads(claude_json.read_text()) if claude_json.exists() else {}
        projects = data.setdefault("projects", {})
        for workspace in trusted_workspaces:
            entry = projects.setdefault(str(workspace), {})
            entry.setdefault("allowedTools", [])
            entry["hasTrustDialogAccepted"] = True
            entry["hasCompletedProjectOnboarding"] = True
            entry.setdefault("projectOnboardingSeenCount", 0)
        claude_json.parent.mkdir(parents=True, exist_ok=True)
        claude_json.write_text(json.dumps(data))

    config_path = claude_dir / "config.json"
    source_config = source_home / ".claude" / "config.json"
    if agent_home != source_home and not config_path.exists() and source_config.exists():
        shutil.copy2(source_config, config_path)
    if not config_path.exists():
        config_path.write_text(json.dumps({"hasCompletedOnboarding": True}))
    else:
        config_data = json.loads(config_path.read_text())
        config_data["hasCompletedOnboarding"] = True
        config_path.write_text(json.dumps(config_data))

    settings_path = claude_dir / "settings.json"
    source_settings = source_home / ".claude" / "settings.json"
    if agent_home != source_home and not settings_path.exists() and source_settings.exists():
        shutil.copy2(source_settings, settings_path)
    if not settings_path.exists():
        settings_path.write_text("{}")

    credentials_path = claude_dir / ".credentials.json"
    source_credentials = source_home / ".claude" / ".credentials.json"
    if (
        agent_home != source_home
        and not credentials_path.exists()
        and source_credentials.exists()
    ):
        shutil.copy2(source_credentials, credentials_path)


__all__ = ["prepare_claude_home"]
