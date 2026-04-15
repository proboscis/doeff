"""Runtime policy and lowering for agent launch effects."""

from dataclasses import dataclass, field
from pathlib import Path

from doeff_agents.effects.agent import ClaudeLaunchEffect, LaunchTaskEffect


@dataclass(frozen=True, kw_only=True)
class ClaudeRuntimePolicy:
    """Runtime-owned Claude selection and environment policy."""

    model: str | None = None
    agent_home: Path | None = None
    trusted_workspaces: tuple[Path, ...] = ()
    bootstrap_exports: dict[str, str] = field(default_factory=dict)


def lower_task_launch_to_claude(
    effect: LaunchTaskEffect,
    policy: ClaudeRuntimePolicy,
) -> ClaudeLaunchEffect:
    """Lower a generic task intent to a Claude-specific launch effect."""

    trusted = policy.trusted_workspaces or (effect.task.work_dir,)
    return ClaudeLaunchEffect(
        session_name=effect.session_name,
        task=effect.task,
        tags=effect.tags,
        ready_timeout_sec=effect.ready_timeout_sec,
        model=policy.model,
        agent_home=policy.agent_home,
        trusted_workspaces=trusted,
        bootstrap_exports=dict(policy.bootstrap_exports),
    )


__all__ = [
    "ClaudeRuntimePolicy",
    "lower_task_launch_to_claude",
]
