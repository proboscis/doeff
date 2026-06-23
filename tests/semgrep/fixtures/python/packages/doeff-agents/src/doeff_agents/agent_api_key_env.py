from doeff_agents import ClaudeRuntimePolicy, LaunchEffect
from doeff_agents.adapters.base import AgentType


def bad_launch(work_dir):
    return LaunchEffect(
        session_name="bad-agent",
        agent_type=AgentType.CLAUDE,
        work_dir=work_dir,
        prompt="run",
        session_env={"ANTHROPIC_API_KEY": "secret"},
    )


def bad_policy():
    return ClaudeRuntimePolicy(
        bootstrap_exports={"anthropic_api_key__personal": "secret"},
    )
