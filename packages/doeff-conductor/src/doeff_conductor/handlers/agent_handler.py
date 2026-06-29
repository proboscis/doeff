"""Agent handler for doeff-conductor."""

import secrets
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from doeff_agents.handlers.daemon import AgentdSessionClient

    from doeff_conductor.effects.agent import AgentEffect
    from doeff_conductor.types import Workspace


WorkspaceResolver = Callable[["Workspace"], Path]


class AgentBackend(Protocol):
    """Strategy object that executes conductor agent effects."""

    def handle_agent(
        self,
        effect: "AgentEffect",
        workspace_resolver: WorkspaceResolver,
    ) -> object:
        """Handle one agent effect."""


class AgentdAgentBackend:
    """Agent backend backed by doeff-agentd."""

    def __init__(self, *, client: "AgentdSessionClient | None" = None) -> None:
        self._client = client

    def handle_agent(
        self,
        effect: "AgentEffect",
        workspace_resolver: WorkspaceResolver,
    ) -> object:
        """Handle schema-validated Agent effect via doeff-agentd."""
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

        client = self._client if self._client is not None else LazyAgentdClient()
        try:
            agent_type = AgentType(effect.task.agent_type)
        except ValueError as exc:
            raise ValueError(f"unsupported agent_type: {effect.task.agent_type}") from exc

        handler = DaemonAgentHandler(client=client)
        return handler.handle_agent(
            AgentsAgentEffect(
                task=AgentsAgentTask(
                    run_id=effect.task.run_id,
                    # The identity-qualified key, NOT the bare node id: the
                    # L2 task derives the session name from this, and the
                    # name must change when the resolved identity does
                    # (see AgentTask.session_node_key).
                    node_id=effect.task.session_node_key,
                    attempt=effect.task.attempt,
                    agent_type=agent_type,
                    work_dir=workspace_resolver(effect.task.env),
                    prompt=effect.task.worker_prompt,
                    result_schema=effect.task.result_schema,
                    model=effect.task.model,
                    effort=effect.task.effort,
                    max_retries=effect.task.max_retries,
                    deadline_seconds=effect.task.deadline_seconds,
                )
            )
        )


class AgentHandler:
    """Handler for schema-validated conductor agent effects."""

    def __init__(
        self,
        workflow_id: str | None = None,
        *,
        workspace_resolver: WorkspaceResolver | None = None,
        backend: AgentBackend | None = None,
    ) -> None:
        self.workflow_id = workflow_id or secrets.token_hex(4)
        self._workspace_resolver = workspace_resolver
        self._backend = backend or AgentdAgentBackend()

    def _resolve_workspace_path(self, workspace: "Workspace") -> Path:
        if self._workspace_resolver is None:
            raise ValueError("Agent workspace requires a workspace resolver")
        return self._workspace_resolver(workspace)

    def handle_agent(self, effect: "AgentEffect") -> object:
        """Handle schema-validated Agent effect via the injected backend."""
        return self._backend.handle_agent(effect, self._resolve_workspace_path)


__all__ = [
    "AgentBackend",
    "AgentHandler",
    "AgentdAgentBackend",
]
