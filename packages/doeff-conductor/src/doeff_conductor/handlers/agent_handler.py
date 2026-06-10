"""
Agent handler for doeff-conductor.
"""

import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..effects.agent import AgentEffect


class AgentHandler:
    """Handler for schema-validated conductor agent effects."""

    def __init__(self, workflow_id: str | None = None):
        self.workflow_id = workflow_id or secrets.token_hex(4)

    def handle_agent(self, effect: "AgentEffect") -> object:
        """Handle schema-validated Agent effect via doeff-agents."""
        from doeff_agents import (
            AgentEffect as AgentsAgentEffect,
        )
        from doeff_agents import (
            AgentTask as AgentsAgentTask,
        )
        from doeff_agents import (
            AgentType,
            DaemonAgentHandler,
            LazyAgentdClient,
        )

        try:
            agent_type = AgentType(effect.task.agent_type)
        except ValueError as exc:
            raise ValueError(f"unsupported agent_type: {effect.task.agent_type}") from exc

        handler = DaemonAgentHandler(client=LazyAgentdClient())
        return handler.handle_agent(
            AgentsAgentEffect(
                task=AgentsAgentTask(
                    run_id=effect.task.run_id,
                    node_id=effect.task.node_id,
                    attempt=effect.task.attempt,
                    agent_type=agent_type,
                    work_dir=effect.task.env.path,
                    prompt=effect.task.prompt,
                    result_schema=effect.task.result_schema,
                    model=effect.task.model,
                    effort=effect.task.effort,
                    max_retries=effect.task.max_retries,
                    timeout_seconds=effect.task.timeout_seconds,
                )
            )
        )


__all__ = ["AgentHandler"]
