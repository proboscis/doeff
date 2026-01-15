"""Tests for agent handler."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from doeff_conductor.effects.agent import (
    CaptureOutput,
    RunAgent,
    SendMessage,
    SpawnAgent,
    WaitForStatus,
)
from doeff_conductor.exceptions import AgentTimeoutError
from doeff_conductor.handlers.agent_handler import AgentHandler
from doeff_conductor.types import AgentRef, WorktreeEnv


@pytest.fixture
def worktree_env(tmp_path: Path) -> WorktreeEnv:
    return WorktreeEnv(
        id="test-env",
        path=tmp_path,
        branch="test-branch",
        base_commit="abc123",
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def agent_ref() -> AgentRef:
    return AgentRef(
        id="session-123",
        name="test-agent",
        workflow_id="wf-001",
        env_id="test-env",
        agent_type="opencode",
    )


class TestAgentHandler:

    @pytest.fixture
    def handler(self) -> AgentHandler:
        return AgentHandler(workflow_id="test-workflow")

    @patch("doeff_agentic.OpenCodeHandler")
    def test_run_agent_returns_output(
        self, mock_handler_class: MagicMock, handler: AgentHandler, worktree_env: WorktreeEnv
    ):
        mock_handler = MagicMock()
        mock_handler_class.return_value = mock_handler

        mock_env_handle = MagicMock(id="env-001")
        mock_session = MagicMock(id="session-001")
        mock_message = MagicMock(role="assistant", content="Task completed successfully")

        mock_handler.handle_create_environment.return_value = mock_env_handle
        mock_handler.handle_create_session.return_value = mock_session
        mock_handler.handle_send_message.return_value = None
        mock_handler.handle_get_messages.return_value = [mock_message]

        effect = RunAgent(env=worktree_env, prompt="Do something")
        result = handler.handle_run_agent(effect)

        assert result == "Task completed successfully"

    @patch("doeff_agentic.OpenCodeHandler")
    def test_run_agent_empty_output(
        self, mock_handler_class: MagicMock, handler: AgentHandler, worktree_env: WorktreeEnv
    ):
        mock_handler = MagicMock()
        mock_handler_class.return_value = mock_handler

        mock_env_handle = MagicMock(id="env-001")
        mock_session = MagicMock(id="session-001")

        mock_handler.handle_create_environment.return_value = mock_env_handle
        mock_handler.handle_create_session.return_value = mock_session
        mock_handler.handle_send_message.return_value = None
        mock_handler.handle_get_messages.return_value = []

        effect = RunAgent(env=worktree_env, prompt="Do something")
        result = handler.handle_run_agent(effect)

        assert result == ""

    @patch("doeff_agentic.OpenCodeHandler")
    def test_spawn_agent_returns_ref(
        self, mock_handler_class: MagicMock, handler: AgentHandler, worktree_env: WorktreeEnv
    ):
        mock_handler = MagicMock()
        mock_handler_class.return_value = mock_handler

        mock_env_handle = MagicMock(id="env-001")
        mock_session = MagicMock(id="session-001")

        mock_handler.handle_create_environment.return_value = mock_env_handle
        mock_handler.handle_create_session.return_value = mock_session
        mock_handler.handle_send_message.return_value = None

        effect = SpawnAgent(env=worktree_env, prompt="Background task", name="bg-agent")
        ref = handler.handle_spawn_agent(effect)

        assert isinstance(ref, AgentRef)
        assert ref.id == "session-001"
        assert ref.name == "bg-agent"
        assert ref.workflow_id == "test-workflow"

    @patch("doeff_agentic.OpenCodeHandler")
    def test_send_message(
        self, mock_handler_class: MagicMock, handler: AgentHandler, agent_ref: AgentRef
    ):
        mock_handler = MagicMock()
        mock_handler_class.return_value = mock_handler

        effect = SendMessage(agent_ref=agent_ref, message="Continue working")
        handler.handle_send_message(effect)

        mock_handler.handle_send_message.assert_called_once()

    @patch("doeff_agentic.OpenCodeHandler")
    def test_wait_for_status_success(
        self, mock_handler_class: MagicMock, handler: AgentHandler, agent_ref: AgentRef
    ):
        mock_handler = MagicMock()
        mock_handler_class.return_value = mock_handler

        from doeff_agentic import AgenticSessionStatus

        mock_handler.handle_get_session_status.return_value = AgenticSessionStatus.DONE

        effect = WaitForStatus(
            agent_ref=agent_ref,
            target=AgenticSessionStatus.DONE,
            timeout=10.0,
        )
        status = handler.handle_wait_for_status(effect)

        assert status == AgenticSessionStatus.DONE

    @patch("time.time")
    @patch("time.sleep")
    @patch("doeff_agentic.OpenCodeHandler")
    def test_wait_for_status_timeout(
        self,
        mock_handler_class: MagicMock,
        mock_sleep: MagicMock,
        mock_time: MagicMock,
        handler: AgentHandler,
        agent_ref: AgentRef,
    ):
        mock_handler = MagicMock()
        mock_handler_class.return_value = mock_handler

        from doeff_agentic import AgenticSessionStatus

        mock_status = MagicMock()
        mock_status.is_terminal.return_value = False
        mock_handler.handle_get_session_status.return_value = mock_status

        mock_time.side_effect = [0, 5, 11]

        effect = WaitForStatus(
            agent_ref=agent_ref,
            target=AgenticSessionStatus.DONE,
            timeout=10.0,
        )

        with pytest.raises(AgentTimeoutError) as exc_info:
            handler.handle_wait_for_status(effect)

        assert exc_info.value.agent_id == "session-123"
        assert exc_info.value.timeout == 10.0

    @patch("doeff_agentic.OpenCodeHandler")
    def test_capture_output(
        self, mock_handler_class: MagicMock, handler: AgentHandler, agent_ref: AgentRef
    ):
        mock_handler = MagicMock()
        mock_handler_class.return_value = mock_handler

        mock_messages = [
            MagicMock(role="user", content="Do task"),
            MagicMock(role="assistant", content="Working on it"),
            MagicMock(role="assistant", content="Done"),
        ]
        mock_handler.handle_get_messages.return_value = mock_messages

        effect = CaptureOutput(agent_ref=agent_ref, lines=10)
        output = handler.handle_capture_output(effect)

        assert "[user] Do task" in output
        assert "[assistant] Working on it" in output
        assert "[assistant] Done" in output


class TestAgentTimeoutError:

    def test_error_attributes(self):
        error = AgentTimeoutError(
            agent_id="agent-123",
            timeout=30.0,
            last_status="running",
        )

        assert error.agent_id == "agent-123"
        assert error.timeout == 30.0
        assert error.last_status == "running"
        assert "agent-123" in str(error)
        assert "30.0" in str(error)
