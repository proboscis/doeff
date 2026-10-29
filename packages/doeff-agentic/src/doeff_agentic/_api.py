"""
High-level API for doeff-agentic.

This module provides the AgenticAPI class, the primary interface for
programmatic access to agentic workflow management.

Usage:
    from doeff_agentic._api import AgenticAPI

    api = AgenticAPI()

    # List workflows
    workflows = api.list_workflows(status=["running", "blocked"])

    # Get workflow by ID or prefix
    wf = api.get_workflow("a3f")

    # Watch workflow (generator for real-time updates)
    for update in api.watch("a3f"):
        print(update.status, update.current_agent)

    # Agent operations
    api.attach("a3f")
    api.send_message("a3f", "continue")
    api.stop("a3f")
"""


import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._event_log import EventLogReader, WorkflowIndex
from ._state import StateManager, generate_workflow_id, get_default_state_dir
from ._types import (
    AgenticEnvironmentHandle,
    AgentStatus,
    WatchEventType,
    WatchUpdate,
    WorkflowInfo,
    WorkflowStatus,
)

# Where the worktree and copy environments put their directories (handlers/opencode.py)
_WORKTREE_ROOT = Path("/tmp/doeff/worktrees")
_COPIES_ROOT = Path("/tmp/doeff/copies")


class AgenticAPI:
    """High-level API for agentic workflow management.

    This is the primary interface for:
    - Listing and querying workflows
    - Watching workflows in real-time
    - Interacting with agent sessions
    - Running new workflows

    Thread Safety:
        This class is NOT thread-safe. Use separate instances per thread.
    """

    def __init__(self, state_dir: Path | str | None = None, *, backend: Any | None = None):
        """Initialize the API.

        Args:
            state_dir: Directory for state files (defaults to XDG state dir)
        """
        self.state_dir = Path(state_dir) if state_dir else get_default_state_dir()
        self._state_manager = StateManager(self.state_dir)
        self._backend = backend

    def _session_backend(self) -> Any:
        if self._backend is not None:
            return self._backend

        try:
            from doeff_agents.tmux import get_default_backend
        except ImportError as exc:
            raise RuntimeError(
                "tmux-backed AgenticAPI operations require doeff-agents. "
                "Install doeff-agentic[legacy] or add doeff-agents to the environment."
            ) from exc

        self._backend = get_default_backend()
        return self._backend

    def list_workflows(
        self,
        status: list[str] | list[WorkflowStatus] | None = None,
        agent_status: list[str] | list[AgentStatus] | None = None,
    ) -> list[WorkflowInfo]:
        """List workflows with optional filtering.

        Args:
            status: Filter by workflow status (e.g., ["running", "blocked"])
            agent_status: Filter by agent status (e.g., ["blocked"])

        Returns:
            List of WorkflowInfo sorted by updated_at descending
        """
        # Normalize status filters
        workflow_status: list[WorkflowStatus] | None = None
        if status:
            workflow_status = [
                s if isinstance(s, WorkflowStatus) else WorkflowStatus(s)
                for s in status
            ]

        agent_st: list[AgentStatus] | None = None
        if agent_status:
            agent_st = [
                s if isinstance(s, AgentStatus) else AgentStatus(s)
                for s in agent_status
            ]

        return self._state_manager.list_workflows(
            status=workflow_status,
            agent_status=agent_st,
        )

    def get_workflow(self, workflow_id: str) -> WorkflowInfo | None:
        """Get workflow by ID or prefix.

        Args:
            workflow_id: Full or prefix workflow ID

        Returns:
            WorkflowInfo if found, None otherwise

        Raises:
            ValueError: If prefix is ambiguous
        """
        return self._state_manager.read_workflow(workflow_id)

    def watch(
        self,
        workflow_id: str,
        poll_interval: float = 1.0,
    ) -> Iterator[WatchUpdate]:
        """Watch a workflow for changes.

        Yields updates whenever the workflow state changes.

        Args:
            workflow_id: Full or prefix workflow ID
            poll_interval: How often to poll for changes

        Yields:
            WatchUpdate for each change
        """
        last_status: WorkflowStatus | None = None
        last_agent: str | None = None
        last_slog: dict[str, Any] | None = None

        for workflow in self._state_manager.watch_workflow(workflow_id, poll_interval):
            # Determine event type
            event = WatchEventType.STATUS_CHANGE
            data: dict[str, Any] = {}

            if last_status is not None and workflow.status != last_status:
                event = WatchEventType.STATUS_CHANGE
                data = {"old": last_status.value, "new": workflow.status.value}
            elif workflow.current_agent != last_agent:
                event = WatchEventType.AGENT_CHANGE
                data = {"old": last_agent, "new": workflow.current_agent}
            elif workflow.last_slog != last_slog and workflow.last_slog:
                event = WatchEventType.SLOG
                data = workflow.last_slog

            last_status = workflow.status
            last_agent = workflow.current_agent
            last_slog = workflow.last_slog

            yield WatchUpdate(
                workflow=workflow,
                event=event,
                data=data,
            )

    def attach(
        self,
        workflow_id: str,
        agent: str | None = None,
    ) -> None:
        """Attach to an agent's tmux session.

        This replaces the current process with tmux attach.

        Args:
            workflow_id: Full or prefix workflow ID
            agent: Specific agent name (uses current_agent if not specified)

        Raises:
            ValueError: If workflow or agent not found
        """
        workflow = self.get_workflow(workflow_id)
        if workflow is None:
            raise ValueError(f"Workflow not found: {workflow_id}")

        # Determine which agent to attach to
        if agent is None:
            agent = workflow.current_agent

        if agent is None and workflow.agents:
            # Use most recent agent
            agent = workflow.agents[0].name

        if agent is None:
            raise ValueError("No agent to attach to")

        # Find the session name
        session_name: str | None = None
        for a in workflow.agents:
            if a.name == agent:
                session_name = a.session_name
                break

        if session_name is None:
            # Try constructing from convention
            session_name = f"doeff-{workflow.id}-{agent}"

        self._session_backend().attach_session(session_name)

    def send_message(
        self,
        workflow_id: str,
        message: str,
        agent: str | None = None,
    ) -> bool:
        """Send a message to a running agent.

        Args:
            workflow_id: Full or prefix workflow ID
            message: Message to send
            agent: Specific agent name (uses current_agent if not specified)

        Returns:
            True if message was sent
        """
        workflow = self.get_workflow(workflow_id)
        if workflow is None:
            return False

        # Determine target agent
        if agent is None:
            agent = workflow.current_agent
        if agent is None:
            return False

        if self.input_requested(workflow.id):
            # This is a response to WaitForUserInput
            self.answer_input(workflow.id, message)
            return True

        return self.send_to_agent(workflow.id, agent, message)

    def input_requested(self, workflow_id: str) -> bool:
        """Whether the workflow (its full ID) waits for the user's input (WaitForUserInput)."""
        return (self.state_dir / "workflows" / workflow_id / "input_request.json").exists()

    def answer_input(self, workflow_id: str, text: str) -> None:
        """Answer the user's input the workflow (its full ID) waits for."""
        (self.state_dir / "workflows" / workflow_id / "input_response.txt").write_text(text)

    def send_to_agent(self, workflow_id: str, agent: str, message: str) -> bool:
        """Type a message into an agent's session and press Enter.

        Args:
            workflow_id: Full or prefix workflow ID
            agent: Agent name
            message: Message to send

        Returns:
            True if the session took the keys
        """
        workflow = self.get_workflow(workflow_id)
        if workflow is None:
            return False

        recorded = next((a for a in workflow.agents if a.name == agent), None)
        if recorded is None:
            target = f"doeff-{workflow.id}-{agent}"
        else:
            target = recorded.pane_id or recorded.session_name

        try:
            self._session_backend().send_keys(target, message, literal=True, enter=True)
        except Exception:
            return False
        return True

    def stop(self, workflow_id: str) -> list[str]:
        """Stop a workflow and all its agents.

        Args:
            workflow_id: Full or prefix workflow ID

        Returns:
            List of stopped agent names
        """
        workflow = self.get_workflow(workflow_id)
        if workflow is None:
            return []

        stopped: list[str] = []
        backend = self._session_backend()
        for agent in workflow.agents:
            try:
                backend.kill_session(agent.session_name)
                stopped.append(agent.name)
            except Exception:
                continue

        # Mark as stopped; the last status and the error stay recorded
        self._state_manager.write_workflow_meta(
            replace(
                workflow,
                status=WorkflowStatus.STOPPED,
                updated_at=datetime.now(timezone.utc),
                current_agent=None,
            )
        )

        return stopped

    def get_agent_output(
        self,
        workflow_id: str,
        agent: str | None = None,
        lines: int = 100,
    ) -> str:
        """Get captured output from an agent.

        Args:
            workflow_id: Full or prefix workflow ID
            agent: Specific agent name (uses current_agent if not specified)
            lines: Number of lines to capture

        Returns:
            Agent output string
        """
        workflow = self.get_workflow(workflow_id)
        if workflow is None:
            return ""

        # Determine target agent
        if agent is None:
            agent = workflow.current_agent
        if agent is None and workflow.agents:
            agent = workflow.agents[0].name
        if agent is None:
            return ""

        # Find pane_id
        pane_id: str | None = None
        for a in workflow.agents:
            if a.name == agent:
                pane_id = a.pane_id
                break

        if pane_id is None:
            session_name = f"doeff-{workflow.id}-{agent}"
            pane_id = session_name

        try:
            return self._session_backend().capture_pane(pane_id, lines)
        except Exception:
            return ""

    def run(
        self,
        workflow,  # Generator/Program from doeff
        name: str | None = None,
        workflow_id: str | None = None,
    ) -> str:
        """Run a workflow.

        Args:
            workflow: doeff Program (generator) to run
            name: Workflow name
            workflow_id: Optional workflow ID (auto-generated if not provided)

        Returns:
            Workflow ID
        """
        from doeff_agents import SessionBackend
        from doeff_agents.session_backend import default_session_backend
        from doeff_core_effects import (
            await_handler,
            lazy_ask,
            listen_handler,
            scheduled,
            slog_handler,
            state,
            try_handler,
            writer,
        )
        from doeff_time import sync_time_handler

        from doeff import do, run
        from doeff import handler as _program_handler

        from ._handler import with_agentic_effectful_handlers

        @do
        def local_session_backend():
            """The terminal backend doeff-agents' runtime asks for, built on its first Launch."""
            return default_session_backend()

        wf_name = name or "unnamed"
        wf_id = workflow_id or generate_workflow_id(wf_name)

        wrapped = with_agentic_effectful_handlers(
            program=workflow,
            workflow_id=wf_id,
            workflow_name=wf_name,
            state_dir=self.state_dir,
        )
        # Entry-point stack, mirroring doeff.cli.run_services.default_interpreter —
        # there is deliberately no public bundled default stack (ADR-DOE-PRESET-001).
        entry_handlers = [
            # The agent runtime (doeff-agents' tmux handler) reads its backend with Ask.
            lazy_ask({SessionBackend: local_session_backend()}),
            state(),
            writer,
            try_handler,
            slog_handler,
            listen_handler,
            await_handler,
            # The agentic handler polls with doeff_time Delay.
            sync_time_handler(),
        ]
        for h in reversed(entry_handlers):
            wrapped = _program_handler(h)(wrapped)

        try:
            # run() raises on failure; the failure path below records it.
            run(scheduled(wrapped))
        except Exception as e:
            # Update workflow as failed
            workflow_info = self.get_workflow(wf_id)
            if workflow_info:
                updated = WorkflowInfo(
                    id=workflow_info.id,
                    name=workflow_info.name,
                    status=WorkflowStatus.FAILED,
                    started_at=workflow_info.started_at,
                    updated_at=datetime.now(timezone.utc),
                    current_agent=None,
                    agents=workflow_info.agents,
                    error=str(e),
                )
                self._state_manager.write_workflow_meta(updated)
            raise

        return wf_id

    def delete(self, workflow_id: str) -> bool:
        """Delete a workflow and its state files.

        Args:
            workflow_id: Full or prefix workflow ID

        Returns:
            True if deleted
        """
        return self._state_manager.delete_workflow(workflow_id)

    def list_environments(
        self, workflow_id: str | None = None
    ) -> list[tuple[str, AgenticEnvironmentHandle, tuple[str, ...]]]:
        """List the environments the workflows' event logs created.

        Args:
            workflow_id: Full or prefix workflow ID (every workflow when None)

        Returns:
            One (workflow ID, environment, names of the sessions bound to it) per environment

        Raises:
            ValueError: If prefix is ambiguous
        """
        reader = EventLogReader(self.state_dir)
        if workflow_id is None:
            workflow_ids = tuple(reader.list_workflows())
        else:
            resolved = WorkflowIndex(self.state_dir).resolve_prefix(workflow_id)
            workflow_ids = (resolved,) if resolved else ()

        recorded = (
            (wf_id, env_id, reader.reconstruct_environment_state(wf_id, env_id))
            for wf_id in workflow_ids
            for env_id in reader.list_environments(wf_id)
        )
        return [
            (wf_id, env, tuple(reader.get_sessions_for_environment(wf_id, env_id)))
            for wf_id, env_id, env in recorded
            if env is not None
        ]

    def cleanup_environments(self, dry_run: bool = False) -> list[str]:
        """Remove every directory under the environments' worktree and copy roots.

        Every directory under /tmp/doeff/worktrees (removed as a git worktree too) and
        /tmp/doeff/copies is removed, whether or not a workflow still uses it.

        Args:
            dry_run: List the directories without removing them

        Returns:
            The directories removed (or that would be removed)
        """
        found = [
            (base_dir, item)
            for base_dir in (_WORKTREE_ROOT, _COPIES_ROOT)
            if base_dir.exists()
            for item in base_dir.iterdir()
            if item.is_dir()
        ]
        if not dry_run:
            for base_dir, item in found:
                if base_dir == _WORKTREE_ROOT:
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", str(item)],
                        capture_output=True,
                        check=False,
                    )
                shutil.rmtree(item, ignore_errors=True)
        return [str(item) for _, item in found]

    def get_trace(self, workflow_id: str) -> list[dict[str, Any]]:
        """Get the effect trace for a workflow.

        Args:
            workflow_id: Full or prefix workflow ID

        Returns:
            List of trace entries
        """
        return self._state_manager.read_trace(workflow_id)


__all__ = ["AgenticAPI"]
