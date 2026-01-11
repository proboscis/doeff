"""
Agent handler for doeff-conductor.

Handles RunAgent, SpawnAgent, SendMessage, WaitForStatus, CaptureOutput effects
by delegating to doeff-agentic.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_agentic import AgenticSessionStatus

    from ..effects.agent import (
        CaptureOutput,
        RunAgent,
        SendMessage,
        SpawnAgent,
        WaitForStatus,
    )
    from ..types import AgentRef, WorktreeEnv


class AgentHandler:
    """Handler for agent effects.

    Delegates to doeff-agentic for actual agent session management.
    """

    def __init__(self, workflow_id: str | None = None):
        """Initialize handler.

        Args:
            workflow_id: Parent workflow ID for tracking.
        """
        self.workflow_id = workflow_id or secrets.token_hex(4)
        self._sessions: dict[str, AgentRef] = {}
        self._opencode_handler = None

    def _get_opencode_handler(self):
        """Lazily initialize OpenCode handler."""
        if self._opencode_handler is None:
            from doeff_agentic import OpenCodeHandler

            self._opencode_handler = OpenCodeHandler(workflow_id=self.workflow_id)
        return self._opencode_handler

    def handle_run_agent(self, effect: RunAgent) -> str:
        """Handle RunAgent effect.

        Spawns an agent and waits for completion.
        """
        from doeff_agentic import (
            AgenticCreateSession,
            AgenticSendMessage,
            AgenticGetMessages,
            AgenticSessionStatus,
        )

        handler = self._get_opencode_handler()

        # Create session
        session_name = effect.name or f"agent-{secrets.token_hex(3)}"

        # Use the handler to create session and send message
        # Since we're in a synchronous handler, we need to use the handler methods directly
        from doeff_agentic import (
            AgenticCreateEnvironment,
            AgenticEnvironmentType,
        )

        # Create environment for the worktree path
        env_effect = AgenticCreateEnvironment(
            env_type=AgenticEnvironmentType.SHARED,
            working_dir=str(effect.env.path),
        )
        env_handle = handler.handle(env_effect)

        # Create session
        session_effect = AgenticCreateSession(
            name=session_name,
            environment_id=env_handle.id,
            agent=effect.agent_type,
        )
        session = handler.handle(session_effect)

        # Send the prompt and wait for completion
        msg_effect = AgenticSendMessage(
            session_id=session.id,
            content=effect.prompt,
            wait=True,
        )
        handler.handle(msg_effect)

        # Get messages to extract output
        messages_effect = AgenticGetMessages(session_id=session.id)
        messages = handler.handle(messages_effect)

        # Return last assistant message content
        for msg in reversed(messages):
            if msg.role == "assistant":
                return msg.content

        return ""

    def handle_spawn_agent(self, effect: SpawnAgent) -> AgentRef:
        """Handle SpawnAgent effect.

        Starts an agent without waiting for completion.
        """
        from doeff_agentic import (
            AgenticCreateEnvironment,
            AgenticCreateSession,
            AgenticEnvironmentType,
            AgenticSendMessage,
        )

        from ..types import AgentRef

        handler = self._get_opencode_handler()

        # Create session name
        session_name = effect.name or f"agent-{secrets.token_hex(3)}"

        # Create environment
        env_effect = AgenticCreateEnvironment(
            env_type=AgenticEnvironmentType.SHARED,
            working_dir=str(effect.env.path),
        )
        env_handle = handler.handle(env_effect)

        # Create session
        session_effect = AgenticCreateSession(
            name=session_name,
            environment_id=env_handle.id,
            agent=effect.agent_type,
        )
        session = handler.handle(session_effect)

        # Send prompt without waiting
        msg_effect = AgenticSendMessage(
            session_id=session.id,
            content=effect.prompt,
            wait=False,
        )
        handler.handle(msg_effect)

        # Create agent ref
        agent_ref = AgentRef(
            id=session.id,
            name=session_name,
            workflow_id=self.workflow_id,
            env_id=effect.env.id,
            agent_type=effect.agent_type,
        )

        self._sessions[session_name] = agent_ref
        return agent_ref

    def handle_send_message(self, effect: SendMessage) -> None:
        """Handle SendMessage effect.

        Sends a message to a running agent.
        """
        from doeff_agentic import AgenticSendMessage

        handler = self._get_opencode_handler()

        msg_effect = AgenticSendMessage(
            session_id=effect.agent_ref.id,
            content=effect.message,
            wait=effect.wait,
        )
        handler.handle(msg_effect)

    def handle_wait_for_status(self, effect: WaitForStatus) -> AgenticSessionStatus:
        """Handle WaitForStatus effect.

        Waits for an agent to reach a specific status.
        """
        import time

        from doeff_agentic import AgenticGetSessionStatus, AgenticSessionStatus

        handler = self._get_opencode_handler()

        # Normalize targets
        targets = effect.target
        if isinstance(targets, AgenticSessionStatus):
            targets = (targets,)

        deadline = None
        if effect.timeout:
            deadline = time.time() + effect.timeout

        while True:
            status_effect = AgenticGetSessionStatus(session_id=effect.agent_ref.id)
            status = handler.handle(status_effect)

            if status in targets:
                return status

            # Check for terminal status not in targets
            if status.is_terminal() and status not in targets:
                return status

            # Check timeout
            if deadline and time.time() > deadline:
                return status

            time.sleep(effect.poll_interval)

    def handle_capture_output(self, effect: CaptureOutput) -> str:
        """Handle CaptureOutput effect.

        Captures output from an agent session.
        """
        from doeff_agentic import AgenticGetMessages

        handler = self._get_opencode_handler()

        messages_effect = AgenticGetMessages(
            session_id=effect.agent_ref.id,
            limit=effect.lines,
        )
        messages = handler.handle(messages_effect)

        # Combine message contents
        output_parts = []
        for msg in messages:
            output_parts.append(f"[{msg.role}] {msg.content}")

        return "\n\n".join(output_parts)


__all__ = ["AgentHandler"]
