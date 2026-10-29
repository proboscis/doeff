"""Agent handler for doeff-conductor."""

from pathlib import Path

from doeff import K, Perform, Resume, handler, run
from doeff.program import ProgramHandler
from doeff_conductor._effects.agent import AgentEffect
from doeff_conductor._handlers._offload import offloaded_call
from doeff_conductor._handlers._workspace_path import WorkspacePath

__all__ = ["agent_handler"]


def _run_on_agentd(effect: AgentEffect, work_dir: Path) -> object:
    """Run one schema-validated conductor agent task on doeff-agentd, blocking until its result.

    It runs off the scheduler thread (``offloaded_call``), so it answers the doeff-agents agent effect with the
    daemon handler in a program of its own.
    """
    from doeff_agents import (
        AgentEffect as AgentsAgentEffect,
    )
    from doeff_agents import (
        AgentTask as AgentsAgentTask,
    )
    from doeff_agents import AgentType, daemon_agent_handler
    from doeff_agents._agentd_client import LazyAgentdClient

    try:
        agent_type = AgentType(effect.task.agent_type)
    except ValueError as exc:
        raise ValueError(f"unsupported agent_type: {effect.task.agent_type}") from exc

    task = AgentsAgentTask(
        run_id=effect.task.run_id,
        # The identity-qualified key, NOT the bare node id: the
        # L2 task derives the session name from this, and the
        # name must change when the resolved identity does
        # (see AgentTask.session_node_key).
        node_id=effect.task.session_node_key,
        attempt=effect.task.attempt,
        agent_type=agent_type,
        work_dir=work_dir,
        prompt=effect.task.worker_prompt,
        result_schema=effect.task.result_schema,
        model=effect.task.model,
        effort=effect.task.effort,
        max_retries=effect.task.max_retries,
        deadline_seconds=effect.task.deadline_seconds,
    )
    on_agentd = daemon_agent_handler(client=LazyAgentdClient())
    return run(on_agentd(Perform(AgentsAgentEffect(task=task))))


def agent_handler() -> ProgramHandler:
    """Build the handler that runs schema-validated conductor agent tasks on doeff-agentd.

    The agent runs in the worktree of its task's workspace, so the handler is installed inside
    the workspace handler (``WorkspacePath``). The blocking wait for the agent's result runs off
    the scheduler thread (``offloaded_call``), so a program using it runs under ``scheduled``
    and sibling agents run in parallel. Each task talks to the daemon through its own client,
    connected on first use.
    """

    @handler
    def answer_agent(effect: AgentEffect, k: K):
        """Run one agent task and answer its validated result artifact."""
        work_dir: Path = yield WorkspacePath(workspace=effect.task.env)
        result = yield offloaded_call(lambda: _run_on_agentd(effect, work_dir))
        return (yield Resume(k, result))

    return answer_agent
