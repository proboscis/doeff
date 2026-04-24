import pytest
from doeff_agents.effects import LaunchTaskEffect
from doeff_agents.runtime import ClaudeRuntimePolicy, lower_task_launch_to_claude


def test_launch_task_lowering_is_deprecated() -> None:
    effect = LaunchTaskEffect(session_name="task-1")
    policy = ClaudeRuntimePolicy()

    with pytest.raises(NotImplementedError, match="LaunchTaskEffect is deprecated"):
        lower_task_launch_to_claude(effect, policy)
