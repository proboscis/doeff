"""Agent handler for doeff-conductor."""

import secrets
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..effects.agent import AgentEffect
    from ..types import Workspace


WorkspaceResolver = Callable[["Workspace"], Path]


class AgentHandler:
    """Handler for schema-validated conductor agent effects."""

    def __init__(
        self,
        workflow_id: str | None = None,
        *,
        workspace_resolver: WorkspaceResolver | None = None,
    ) -> None:
        self.workflow_id = workflow_id or secrets.token_hex(4)
        self._workspace_resolver = workspace_resolver

    def _resolve_workspace_path(self, workspace: "Workspace") -> Path:
        if self._workspace_resolver is None:
            raise ValueError("Agent workspace requires a workspace resolver")
        return self._workspace_resolver(workspace)

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
                    work_dir=self._resolve_workspace_path(effect.task.env),
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
