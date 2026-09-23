"""Claude Code home preparation shared by agent handlers.

段 7 lane 7c(agora-redesign・決定 1.3): 何を作り何を写すかの判断はこの
module に残り、file の読み書きは `doeff_agents.io_effects` の要求になった。
実行する家は本番 `doeff_agents.io_handlers` と検 `doeff_agents.io_fake` の
2 つで、どちらを当てるかは composition root(`TmuxAgentHandler` /
`DaemonAgentHandler` の `io_root`)が選ぶ。
"""

import json
import posixpath
from pathlib import Path

import hy  # noqa: F401  # .hy import hook — the I/O effect vocabulary is a Hy module
from doeff import do

from doeff_agents.io_effects import (
    copy_file,
    home_path,
    make_dirs,
    path_exists,
    read_text,
    write_text,
)
from doeff_agents.io_root import IoGenerator, as_bool, as_optional_str, as_str


def trusted_projects_json(raw_text: str | None, trusted_workspaces: tuple[Path, ...]) -> str:
    """Pure judgment: ``.claude.json`` text with these workspaces pre-trusted."""
    data = json.loads(raw_text) if raw_text else {}
    if not isinstance(data, dict):
        data = {}
    projects = data.setdefault("projects", {})
    for workspace in trusted_workspaces:
        entry = projects.setdefault(str(workspace), {})
        entry.setdefault("allowedTools", [])
        entry["hasTrustDialogAccepted"] = True
        entry["hasCompletedProjectOnboarding"] = True
        entry.setdefault("projectOnboardingSeenCount", 0)
    return json.dumps(data)


def onboarded_config_json(raw_text: str | None) -> str:
    """Pure judgment: ``config.json`` text with onboarding marked complete."""
    data = json.loads(raw_text) if raw_text else {}
    if not isinstance(data, dict):
        data = {}
    data["hasCompletedOnboarding"] = True
    return json.dumps(data)


@do
def _first_existing_path(paths: tuple[str, ...]) -> IoGenerator[str | None]:
    """Program returning the first path that exists, or None."""
    for path in paths:
        found = as_bool((yield path_exists(path)))
        if found:
            return path
    return None


@do
def _seed_from_source(target: str, source: str, agent_home: str, source_home: str) -> IoGenerator[bool]:
    """Program copying a支え file from the caller's home when the agent lacks one."""
    if agent_home == source_home:
        return False
    present = as_bool((yield path_exists(target)))
    if present:
        return False
    yield copy_file(source, target)
    return True


@do
def prepare_claude_home(agent_home: Path, trusted_workspaces: tuple[Path, ...]) -> IoGenerator[None]:
    """Program preparing isolated Claude Code state so launches never block on dialogs."""
    source_home = as_str((yield home_path()))
    agent_root = str(agent_home)
    claude_dir = posixpath.join(agent_root, ".claude")
    yield make_dirs(claude_dir)

    candidate_json_paths = (
        posixpath.join(agent_root, ".claude.json"),
        posixpath.join(claude_dir, ".claude.json"),
    )
    source_candidates = (
        *candidate_json_paths,
        posixpath.join(source_home, ".claude.json"),
        posixpath.join(source_home, ".claude", ".claude.json"),
    )
    source_claude_json = as_optional_str((yield _first_existing_path(source_candidates)))

    for claude_json in candidate_json_paths:
        if source_claude_json is None or source_claude_json == claude_json:
            continue
        present = as_bool((yield path_exists(claude_json)))
        if present:
            continue
        yield make_dirs(posixpath.dirname(claude_json))
        yield copy_file(source_claude_json, claude_json)

    for claude_json in candidate_json_paths:
        raw_text = as_optional_str((yield read_text(claude_json)))
        yield make_dirs(posixpath.dirname(claude_json))
        yield write_text(claude_json, trusted_projects_json(raw_text, trusted_workspaces))

    config_path = posixpath.join(claude_dir, "config.json")
    yield _seed_from_source(
        config_path, posixpath.join(source_home, ".claude", "config.json"), agent_root, source_home
    )
    raw_config = as_optional_str((yield read_text(config_path)))
    yield write_text(config_path, onboarded_config_json(raw_config))

    settings_path = posixpath.join(claude_dir, "settings.json")
    yield _seed_from_source(
        settings_path,
        posixpath.join(source_home, ".claude", "settings.json"),
        agent_root,
        source_home,
    )
    settings_present = as_bool((yield path_exists(settings_path)))
    if not settings_present:
        yield write_text(settings_path, "{}")

    credentials_path = posixpath.join(claude_dir, ".credentials.json")
    yield _seed_from_source(
        credentials_path,
        posixpath.join(source_home, ".claude", ".credentials.json"),
        agent_root,
        source_home,
    )
    return None
