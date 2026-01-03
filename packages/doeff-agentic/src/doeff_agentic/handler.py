"""
Effect handler for agentic workflows.

This module provides the handler that interprets high-level agentic effects
by orchestrating doeff-agents sessions with state management and observability.

The handler:
1. Translates RunAgent/SendMessage/etc. to low-level agent effects
2. Manages workflow state files for CLI/plugin consumers
3. Integrates with doeff-flow for observability
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doeff_agents import (
    AgentType,
    Capture,
    Launch,
    LaunchConfig,
    Monitor,
    Observation,
    Send,
    SessionHandle,
    SessionStatus,
    Sleep,
    Stop,
    TmuxAgentHandler,
)

from .effects import (
    AgentNotRunningError,
    CaptureOutputEffect,
    RunAgentEffect,
    SendMessageEffect,
    StopAgentEffect,
    UserInputTimeoutError,
    WaitForStatusEffect,
    WaitForUserInputEffect,
)
from .state import StateManager, generate_workflow_id
from .types import AgentConfig, AgentInfo, AgentStatus, WorkflowInfo, WorkflowStatus


def _agent_type_from_str(s: str) -> AgentType:
    """Convert string to AgentType enum."""
    s = s.lower()
    if s == "claude":
        return AgentType.CLAUDE
    elif s == "codex":
        return AgentType.CODEX
    elif s == "gemini":
        return AgentType.GEMINI
    else:
        return AgentType.CUSTOM


def _agent_status_from_session_status(s: SessionStatus) -> AgentStatus:
    """Convert SessionStatus to AgentStatus."""
    mapping = {
        SessionStatus.BOOTING: AgentStatus.BOOTING,
        SessionStatus.RUNNING: AgentStatus.RUNNING,
        SessionStatus.BLOCKED: AgentStatus.BLOCKED,
        SessionStatus.DONE: AgentStatus.DONE,
        SessionStatus.FAILED: AgentStatus.FAILED,
        SessionStatus.EXITED: AgentStatus.EXITED,
        SessionStatus.STOPPED: AgentStatus.STOPPED,
    }
    return mapping.get(s, AgentStatus.RUNNING)


@dataclass
class WorkflowContext:
    """Context for a running workflow.

    Tracks the workflow state and active agent sessions.
    """

    workflow_id: str
    workflow_name: str
    state_manager: StateManager
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sessions: dict[str, SessionHandle] = field(default_factory=dict)
    agent_counter: int = field(default=0)


class AgenticHandler:
    """Handler for high-level agentic workflow effects.

    This handler orchestrates:
    - Agent session lifecycle using doeff-agents
    - State file management for CLI consumers
    - Workflow observability

    Usage with CESK interpreter:
        handlers = agentic_effectful_handlers(workflow_id="my-workflow")
        result = run_sync(my_workflow(), handlers=handlers)

    Usage standalone:
        handler = AgenticHandler(workflow_id="my-workflow")
        result = handler.handle_run_agent(RunAgentEffect(...))
    """

    def __init__(
        self,
        workflow_id: str | None = None,
        workflow_name: str | None = None,
        state_dir: Path | str | None = None,
        tmux_handler: TmuxAgentHandler | None = None,
    ):
        """Initialize the agentic handler.

        Args:
            workflow_id: Workflow identifier (auto-generated if not provided)
            workflow_name: Human-readable workflow name
            state_dir: Directory for state files
            tmux_handler: Agent handler (creates default if not provided)
        """
        self.workflow_name = workflow_name or "unnamed"
        self.workflow_id = workflow_id or generate_workflow_id(self.workflow_name)
        self.state_manager = StateManager(state_dir)
        self.tmux_handler = tmux_handler or TmuxAgentHandler()
        self._context = WorkflowContext(
            workflow_id=self.workflow_id,
            workflow_name=self.workflow_name,
            state_manager=self.state_manager,
        )
        self._update_workflow_status(WorkflowStatus.RUNNING)

    def _session_name_for(self, name: str | None) -> str:
        """Generate session name for an agent."""
        if name:
            return f"doeff-{self.workflow_id}-{name}"
        self._context.agent_counter += 1
        return f"doeff-{self.workflow_id}-agent-{self._context.agent_counter}"

    def _update_workflow_status(
        self,
        status: WorkflowStatus,
        current_agent: str | None = None,
        last_slog: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Update workflow status in state files."""
        agents = []
        for name, handle in self._context.sessions.items():
            # Get agent status from observation
            try:
                obs = self.tmux_handler.handle_monitor(Monitor(handle))
                agent_status = _agent_status_from_session_status(obs.status)
            except Exception:
                agent_status = AgentStatus.EXITED

            agents.append(AgentInfo(
                name=name,
                status=agent_status,
                session_name=handle.session_name,
                pane_id=handle.pane_id,
                started_at=handle.started_at,
            ))

        workflow = WorkflowInfo(
            id=self.workflow_id,
            name=self.workflow_name,
            status=status,
            started_at=self._context.started_at,
            updated_at=datetime.now(timezone.utc),
            current_agent=current_agent,
            agents=tuple(agents),
            last_slog=last_slog,
            error=error,
        )
        self.state_manager.write_workflow_meta(workflow)

        # Also update individual agent files
        for agent in agents:
            self.state_manager.write_agent_state(self.workflow_id, agent)

    def handle_run_agent(self, effect: RunAgentEffect) -> str:
        """Handle RunAgent effect.

        Launches an agent, monitors until completion, and returns output.
        """
        config = effect.config
        agent_name = effect.session_name or f"agent-{self._context.agent_counter + 1}"
        session_name = self._session_name_for(effect.session_name)

        # Convert to doeff-agents LaunchConfig
        agent_type = _agent_type_from_str(config.agent_type)
        work_dir = Path(config.work_dir) if config.work_dir else Path.cwd()

        launch_config = LaunchConfig(
            agent_type=agent_type,
            work_dir=work_dir,
            prompt=config.prompt,
            resume=config.resume,
            profile=config.profile,
        )

        # Launch agent
        launch_effect = Launch(
            session_name=session_name,
            config=launch_config,
            ready_timeout=effect.ready_timeout,
        )
        handle = self.tmux_handler.handle_launch(launch_effect)
        self._context.sessions[agent_name] = handle

        # Update workflow status
        self._update_workflow_status(
            WorkflowStatus.RUNNING,
            current_agent=agent_name,
        )

        # Monitor until terminal status
        last_output = ""
        while True:
            obs = self.tmux_handler.handle_monitor(Monitor(handle))

            # Update status on change
            if obs.output_changed or obs.status != SessionStatus.RUNNING:
                status = WorkflowStatus.RUNNING
                if obs.status == SessionStatus.BLOCKED:
                    status = WorkflowStatus.BLOCKED
                self._update_workflow_status(status, current_agent=agent_name)

            # Check for terminal status
            if obs.is_terminal:
                # Capture final output
                last_output = self.tmux_handler.handle_capture(
                    Capture(handle, lines=500)
                )
                break

            # Poll
            self.tmux_handler.handle_sleep(Sleep(effect.poll_interval))

        # Update workflow
        self._update_workflow_status(WorkflowStatus.RUNNING, current_agent=None)

        return last_output

    def handle_send_message(self, effect: SendMessageEffect) -> None:
        """Handle SendMessage effect."""
        # Find session
        handle = self._context.sessions.get(effect.session_name)
        if handle is None:
            # Try full session name lookup
            for name, h in self._context.sessions.items():
                if h.session_name == effect.session_name:
                    handle = h
                    break

        if handle is None:
            raise AgentNotRunningError(effect.session_name, "not_found")

        send_effect = Send(
            handle,
            effect.message,
            enter=effect.enter,
        )
        self.tmux_handler.handle_send(send_effect)

    def handle_wait_for_status(self, effect: WaitForStatusEffect) -> AgentStatus:
        """Handle WaitForStatus effect."""
        handle = self._context.sessions.get(effect.session_name)
        if handle is None:
            raise AgentNotRunningError(effect.session_name, "not_found")

        # Normalize target_status to tuple
        targets = effect.target_status
        if isinstance(targets, AgentStatus):
            targets = (targets,)

        # Map to SessionStatus
        session_targets = set()
        for t in targets:
            if t == AgentStatus.RUNNING:
                session_targets.add(SessionStatus.RUNNING)
            elif t == AgentStatus.BLOCKED:
                session_targets.add(SessionStatus.BLOCKED)
            elif t == AgentStatus.DONE:
                session_targets.add(SessionStatus.DONE)
            elif t == AgentStatus.FAILED:
                session_targets.add(SessionStatus.FAILED)
            elif t == AgentStatus.EXITED:
                session_targets.add(SessionStatus.EXITED)
            elif t == AgentStatus.STOPPED:
                session_targets.add(SessionStatus.STOPPED)

        deadline = time.time() + effect.timeout

        while time.time() < deadline:
            obs = self.tmux_handler.handle_monitor(Monitor(handle))

            if obs.status in session_targets:
                return _agent_status_from_session_status(obs.status)

            # Check for unexpected terminal status
            if obs.is_terminal and obs.status not in session_targets:
                return _agent_status_from_session_status(obs.status)

            self.tmux_handler.handle_sleep(Sleep(effect.poll_interval))

        # Timeout - return current status
        obs = self.tmux_handler.handle_monitor(Monitor(handle))
        return _agent_status_from_session_status(obs.status)

    def handle_capture_output(self, effect: CaptureOutputEffect) -> str:
        """Handle CaptureOutput effect."""
        handle = self._context.sessions.get(effect.session_name)
        if handle is None:
            raise AgentNotRunningError(effect.session_name, "not_found")

        return self.tmux_handler.handle_capture(Capture(handle, lines=effect.lines))

    def handle_wait_for_user_input(self, effect: WaitForUserInputEffect) -> str:
        """Handle WaitForUserInput effect.

        Blocks until user provides input via CLI or TUI.
        """
        handle = self._context.sessions.get(effect.session_name)
        if handle is None:
            raise AgentNotRunningError(effect.session_name, "not_found")

        # Update workflow to blocked status with prompt
        self._update_workflow_status(
            WorkflowStatus.BLOCKED,
            current_agent=effect.session_name,
            last_slog={"status": "waiting-input", "prompt": effect.prompt},
        )

        # Write a marker file for the CLI to detect
        workflow_dir = self.state_manager.state_dir / "workflows" / self.workflow_id
        input_request_file = workflow_dir / "input_request.json"
        input_response_file = workflow_dir / "input_response.txt"

        import json
        input_request_file.write_text(json.dumps({
            "session_name": effect.session_name,
            "prompt": effect.prompt,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }))

        # Wait for response
        deadline = None
        if effect.timeout:
            deadline = time.time() + effect.timeout

        while True:
            if deadline and time.time() > deadline:
                input_request_file.unlink(missing_ok=True)
                raise UserInputTimeoutError("wait_for_user_input", effect.timeout or 0)

            if input_response_file.exists():
                response = input_response_file.read_text()
                input_response_file.unlink()
                input_request_file.unlink(missing_ok=True)

                # Update workflow back to running
                self._update_workflow_status(
                    WorkflowStatus.RUNNING,
                    current_agent=effect.session_name,
                )
                return response

            time.sleep(0.5)

    def handle_stop_agent(self, effect: StopAgentEffect) -> None:
        """Handle StopAgent effect."""
        handle = self._context.sessions.get(effect.session_name)
        if handle is None:
            return  # Already stopped or not found

        self.tmux_handler.handle_stop(Stop(handle))
        del self._context.sessions[effect.session_name]

        self._update_workflow_status(WorkflowStatus.RUNNING)

    def complete_workflow(
        self,
        result: Any = None,
        error: Exception | None = None,
    ) -> None:
        """Mark workflow as completed or failed."""
        if error:
            self._update_workflow_status(
                WorkflowStatus.FAILED,
                error=str(error),
            )
        else:
            self._update_workflow_status(WorkflowStatus.COMPLETED)

    def cleanup(self) -> None:
        """Clean up all agent sessions."""
        for name in list(self._context.sessions.keys()):
            handle = self._context.sessions[name]
            try:
                self.tmux_handler.handle_stop(Stop(handle))
            except Exception:
                pass
            del self._context.sessions[name]


def agentic_effectful_handlers(
    workflow_id: str | None = None,
    workflow_name: str | None = None,
    state_dir: Path | str | None = None,
) -> dict[type, Any]:
    """Create CESK-compatible handlers for agentic effects.

    Returns a handler dictionary suitable for use with doeff's run_sync.

    Usage:
        from doeff import run_sync
        from doeff_agentic import agentic_effectful_handlers

        handlers = agentic_effectful_handlers(workflow_id="my-workflow")
        result = run_sync(my_workflow(), handlers=handlers)
    """
    handler = AgenticHandler(
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        state_dir=state_dir,
    )

    return {
        RunAgentEffect: lambda eff: handler.handle_run_agent(eff),
        SendMessageEffect: lambda eff: handler.handle_send_message(eff),
        WaitForStatusEffect: lambda eff: handler.handle_wait_for_status(eff),
        CaptureOutputEffect: lambda eff: handler.handle_capture_output(eff),
        WaitForUserInputEffect: lambda eff: handler.handle_wait_for_user_input(eff),
        StopAgentEffect: lambda eff: handler.handle_stop_agent(eff),
    }


def agent_handler(
    workflow_id: str | None = None,
    workflow_name: str | None = None,
    state_dir: Path | str | None = None,
) -> AgenticHandler:
    """Create an agentic handler instance.

    This is the primary way to get a handler for standalone use.

    Args:
        workflow_id: Workflow identifier (auto-generated if not provided)
        workflow_name: Human-readable workflow name
        state_dir: Directory for state files

    Returns:
        AgenticHandler instance
    """
    return AgenticHandler(
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        state_dir=state_dir,
    )


__all__ = [
    "AgenticHandler",
    "WorkflowContext",
    "agent_handler",
    "agentic_effectful_handlers",
]
