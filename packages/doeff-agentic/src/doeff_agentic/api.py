"""
High-level API for doeff-agentic.

This module provides the AgenticAPI class, the primary interface for
programmatic access to agentic workflow management.

Usage:
    from doeff_agentic.api import AgenticAPI

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

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

from .state import StateManager, generate_workflow_id, get_default_state_dir
from .types import (
    AgentConfig,
    AgentStatus,
    WatchEventType,
    WatchUpdate,
    WorkflowInfo,
    WorkflowStatus,
)


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

    def __init__(self, state_dir: Path | str | None = None):
        """Initialize the API.

        Args:
            state_dir: Directory for state files (defaults to XDG state dir)
        """
        self.state_dir = Path(state_dir) if state_dir else get_default_state_dir()
        self._state_manager = StateManager(self.state_dir)

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

        # Check if we're in tmux
        in_tmux = "TMUX" in os.environ

        if in_tmux:
            # Switch client
            subprocess.run(["tmux", "switch-client", "-t", session_name])
        else:
            # Attach
            subprocess.run(["tmux", "attach-session", "-t", session_name])

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

        # Find session name
        session_name: str | None = None
        pane_id: str | None = None
        for a in workflow.agents:
            if a.name == agent:
                session_name = a.session_name
                pane_id = a.pane_id
                break

        if session_name is None:
            session_name = f"doeff-{workflow.id}-{agent}"

        # Check if this is a user input response
        input_response_file = (
            self.state_dir / "workflows" / workflow.id / "input_response.txt"
        )
        if (self.state_dir / "workflows" / workflow.id / "input_request.json").exists():
            # This is a response to WaitForUserInput
            input_response_file.write_text(message)
            return True

        # Send via tmux
        target = pane_id or session_name
        result = subprocess.run(
            ["tmux", "send-keys", "-t", target, "-l", message, "Enter"],
            capture_output=True,
        )
        return result.returncode == 0

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
        for agent in workflow.agents:
            result = subprocess.run(
                ["tmux", "kill-session", "-t", agent.session_name],
                capture_output=True,
            )
            if result.returncode == 0:
                stopped.append(agent.name)

        # Update workflow status
        if workflow:
            # Mark as stopped
            from datetime import datetime, timezone
            updated = WorkflowInfo(
                id=workflow.id,
                name=workflow.name,
                status=WorkflowStatus.STOPPED,
                started_at=workflow.started_at,
                updated_at=datetime.now(timezone.utc),
                current_agent=None,
                agents=(),
                error=None,
            )
            self._state_manager.write_workflow_meta(updated)

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

        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", pane_id, "-S", f"-{lines}"],
            capture_output=True,
            text=True,
        )
        return result.stdout if result.returncode == 0 else ""

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
        from doeff import run_sync
        from .handler import agentic_effectful_handlers

        wf_name = name or "unnamed"
        wf_id = workflow_id or generate_workflow_id(wf_name)

        handlers = agentic_effectful_handlers(
            workflow_id=wf_id,
            workflow_name=wf_name,
            state_dir=self.state_dir,
        )

        try:
            run_sync(workflow, handlers=handlers)
        except Exception as e:
            # Update workflow as failed
            workflow_info = self.get_workflow(wf_id)
            if workflow_info:
                from datetime import datetime, timezone
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

    def get_trace(self, workflow_id: str) -> list[dict[str, Any]]:
        """Get the effect trace for a workflow.

        Args:
            workflow_id: Full or prefix workflow ID

        Returns:
            List of trace entries
        """
        return self._state_manager.read_trace(workflow_id)


__all__ = ["AgenticAPI"]
