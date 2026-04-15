from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents.effects import (
    AgentTaskSpec,
    ClaudeLaunchEffect,
    ExpectedArtifact,
    LaunchTask,
    WorkspaceFile,
)
from doeff_agents.runtime import ClaudeRuntimePolicy, lower_task_launch_to_claude


def test_launch_task_is_implementation_neutral() -> None:
    effect = LaunchTask(
        "task-1",
        AgentTaskSpec(
            work_dir=Path("/tmp/work"),
            instructions="Do the thing",
            workspace_files=(
                WorkspaceFile(relative_path=Path("CLAUDE.md"), content="hello"),
            ),
            expected_artifacts=(
                ExpectedArtifact(relative_path=Path("result.json")),
            ),
        ),
        tags=("nakagawa", "trade_execution"),
        ready_timeout_sec=12.0,
    )

    assert effect.session_name == "task-1"
    assert effect.task.work_dir == Path("/tmp/work")
    assert effect.task.instructions == "Do the thing"
    assert effect.tags == ("nakagawa", "trade_execution")
    assert effect.ready_timeout_sec == 12.0
    assert not hasattr(effect, "agent_type")
    assert not hasattr(effect, "model")


def test_lower_task_launch_to_claude_applies_runtime_policy() -> None:
    effect = LaunchTask(
        "task-2",
        AgentTaskSpec(
            work_dir=Path("/tmp/work"),
            instructions="Do the thing",
        ),
        tags=("paper", "safe"),
        ready_timeout_sec=45.0,
    )
    policy = ClaudeRuntimePolicy(
        model="opus",
        agent_home=Path("/tmp/claude-home"),
        trusted_workspaces=(Path("/tmp/work"),),
        bootstrap_exports={"FOO": "bar"},
    )

    lowered = lower_task_launch_to_claude(effect, policy)

    assert isinstance(lowered, ClaudeLaunchEffect)
    assert lowered.session_name == "task-2"
    assert lowered.task == effect.task
    assert lowered.tags == ("paper", "safe")
    assert lowered.ready_timeout_sec == 45.0
    assert lowered.model == "opus"
    assert lowered.agent_home == Path("/tmp/claude-home")
    assert lowered.trusted_workspaces == (Path("/tmp/work"),)
    assert lowered.bootstrap_exports == {"FOO": "bar"}
