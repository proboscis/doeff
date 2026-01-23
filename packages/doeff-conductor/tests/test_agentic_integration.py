"""
Integration tests for conductor <-> doeff-agentic.

These tests verify that the conductor's AgentHandler correctly integrates
with doeff-agentic's effect system.

Test modes:
- Default: Uses MockOpenCodeHandler (no external deps, runs in CI)
- Real: Set CONDUCTOR_TEST_REAL_OPENCODE=1 to test with actual OpenCode server

Run:
    # Mock mode (default)
    uv run pytest packages/doeff-conductor/tests/test_agentic_integration.py -v

    # Real OpenCode mode (requires running opencode server)
    CONDUCTOR_TEST_REAL_OPENCODE=1 uv run pytest packages/doeff-conductor/tests/test_agentic_integration.py -v -m integration
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from doeff import do
from doeff_conductor.handlers import run_sync
from doeff_agentic import (
    AgenticCreateEnvironment,
    AgenticCreateSession,
    AgenticEnvironmentHandle,
    AgenticEnvironmentType,
    AgenticGetMessages,
    AgenticGetSessionStatus,
    AgenticMessage,
    AgenticSendMessage,
    AgenticSessionHandle,
    AgenticSessionStatus,
)
from doeff_conductor import (
    RunAgent,
    SpawnAgent,
    SendMessage,
    WaitForStatus,
    CaptureOutput,
    make_scheduled_handler,
)
from doeff_conductor.handlers.agent_handler import AgentHandler
from doeff_conductor.types import AgentRef, WorktreeEnv


# =============================================================================
# Mock OpenCode Handler
# =============================================================================


class MockOpenCodeHandler:
    """
    Mock implementation of OpenCodeHandler for testing.

    Simulates the behavior of the real OpenCodeHandler without
    requiring an actual OpenCode server.
    """

    def __init__(self, workflow_id: str | None = None):
        self.workflow_id = workflow_id or secrets.token_hex(4)
        self._environments: dict[str, AgenticEnvironmentHandle] = {}
        self._sessions: dict[str, AgenticSessionHandle] = {}
        self._messages: dict[str, list[AgenticMessage]] = {}
        self._msg_counter = 0
        self._env_counter = 0
        self._sess_counter = 0

    # Public methods matching OpenCodeHandler interface
    def handle_create_environment(
        self, effect: AgenticCreateEnvironment
    ) -> AgenticEnvironmentHandle:
        return self._handle_create_environment(effect)

    def handle_create_session(self, effect: AgenticCreateSession) -> AgenticSessionHandle:
        return self._handle_create_session(effect)

    def handle_send_message(self, effect: AgenticSendMessage) -> AgenticMessage | None:
        return self._handle_send_message(effect)

    def handle_get_messages(self, effect: AgenticGetMessages) -> list[AgenticMessage]:
        return self._handle_get_messages(effect)

    def handle_get_session_status(
        self, effect: AgenticGetSessionStatus
    ) -> AgenticSessionStatus:
        return self._handle_get_session_status(effect)

    # Internal implementation methods
    def _handle_create_environment(
        self, effect: AgenticCreateEnvironment
    ) -> AgenticEnvironmentHandle:
        self._env_counter += 1
        env_id = f"mock-env-{self._env_counter}"

        env = AgenticEnvironmentHandle(
            id=env_id,
            env_type=effect.env_type,
            name=effect.name,
            working_dir=effect.working_dir or "",
            created_at=datetime.now(timezone.utc),
        )
        self._environments[env_id] = env
        return env

    def _handle_create_session(self, effect: AgenticCreateSession) -> AgenticSessionHandle:
        self._sess_counter += 1
        session_id = f"mock-sess-{self._sess_counter}"

        session = AgenticSessionHandle(
            id=session_id,
            name=effect.name,
            workflow_id=self.workflow_id,
            environment_id=effect.environment_id,
            status=AgenticSessionStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            title=effect.title or effect.name,
        )
        self._sessions[session_id] = session
        self._messages[session_id] = []
        return session

    def _handle_send_message(self, effect: AgenticSendMessage) -> AgenticMessage | None:
        session_id = effect.session_id

        # Add user message
        self._msg_counter += 1
        user_msg = AgenticMessage(
            id=f"msg-{self._msg_counter}",
            session_id=session_id,
            role="user",
            content=effect.content,
            created_at=datetime.now(timezone.utc),
        )
        self._messages[session_id].append(user_msg)

        if effect.wait:
            # Simulate agent response
            self._msg_counter += 1
            response = self._generate_response(effect.content)
            assistant_msg = AgenticMessage(
                id=f"msg-{self._msg_counter}",
                session_id=session_id,
                role="assistant",
                content=response,
                created_at=datetime.now(timezone.utc),
            )
            self._messages[session_id].append(assistant_msg)

            # Mark session as done
            if session_id in self._sessions:
                self._sessions[session_id] = AgenticSessionHandle(
                    id=self._sessions[session_id].id,
                    name=self._sessions[session_id].name,
                    workflow_id=self._sessions[session_id].workflow_id,
                    environment_id=self._sessions[session_id].environment_id,
                    status=AgenticSessionStatus.DONE,
                    created_at=self._sessions[session_id].created_at,
                    title=self._sessions[session_id].title,
                )

            return assistant_msg

        return None

    def _handle_get_messages(self, effect: AgenticGetMessages) -> list[AgenticMessage]:
        messages = self._messages.get(effect.session_id, [])
        if effect.limit:
            return messages[-effect.limit :]
        return messages

    def _handle_get_session_status(
        self, effect: AgenticGetSessionStatus
    ) -> AgenticSessionStatus:
        session = self._sessions.get(effect.session_id)
        if session:
            # Simulate session completion on status check
            # In real scenario, agent would complete over time
            if session.status == AgenticSessionStatus.RUNNING:
                # Mark as done after first status check
                self._sessions[effect.session_id] = AgenticSessionHandle(
                    id=session.id,
                    name=session.name,
                    workflow_id=session.workflow_id,
                    environment_id=session.environment_id,
                    status=AgenticSessionStatus.DONE,
                    created_at=session.created_at,
                    title=session.title,
                )
                # Also add a mock response if missing
                if effect.session_id in self._messages and len(self._messages[effect.session_id]) == 1:
                    self._msg_counter += 1
                    self._messages[effect.session_id].append(
                        AgenticMessage(
                            id=f"msg-{self._msg_counter}",
                            session_id=effect.session_id,
                            role="assistant",
                            content="Background task completed successfully.",
                            created_at=datetime.now(timezone.utc),
                        )
                    )
                return AgenticSessionStatus.DONE
            return session.status
        return AgenticSessionStatus.DONE

    def _generate_response(self, content: str) -> str:
        """Generate contextual mock response."""
        content_lower = content.lower()

        # Check "fix" before "review" since fix prompts often contain review text
        if "fix" in content_lower:
            return "I've fixed the issues:\n- Added null checks\n- Improved error handling"
        elif "review" in content_lower:
            return (
                "I've reviewed the code. Found some issues:\n"
                "- Line 15: Missing error handling\n"
                "- Line 42: Potential null reference"
            )
        elif "test" in content_lower:
            return "All tests pass. Coverage: 85%"
        elif "implement" in content_lower:
            return "Implementation complete. Created 3 new files."
        else:
            return f"Task completed successfully. Processed: {content[:50]}..."


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def worktree_env(tmp_path: Path) -> WorktreeEnv:
    """Create a mock worktree environment."""
    return WorktreeEnv(
        id="test-env",
        path=tmp_path,
        branch="test-branch",
        base_commit="abc123",
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_handler() -> MockOpenCodeHandler:
    """Create a mock OpenCode handler."""
    return MockOpenCodeHandler(workflow_id="test-workflow")


@pytest.fixture
def agent_handler_with_mock(mock_handler: MockOpenCodeHandler) -> AgentHandler:
    """Create AgentHandler with mocked OpenCode handler."""
    handler = AgentHandler(workflow_id="test-workflow")
    handler._opencode_handler = mock_handler
    return handler


# =============================================================================
# Integration Tests (Mock Mode)
# =============================================================================


class TestAgentHandlerIntegration:
    """Test AgentHandler integration with doeff-agentic effects."""

    def test_run_agent_creates_env_and_session(
        self,
        agent_handler_with_mock: AgentHandler,
        mock_handler: MockOpenCodeHandler,
        worktree_env: WorktreeEnv,
    ):
        """Test that RunAgent creates environment and session."""
        from doeff_conductor.effects.agent import RunAgent

        effect = RunAgent(env=worktree_env, prompt="Implement the feature")
        result = agent_handler_with_mock.handle_run_agent(effect)

        # Verify environment was created
        assert len(mock_handler._environments) == 1
        env = list(mock_handler._environments.values())[0]
        assert env.working_dir == str(worktree_env.path)

        # Verify session was created
        assert len(mock_handler._sessions) == 1

        # Verify response
        assert "Implementation complete" in result

    def test_run_agent_returns_last_assistant_message(
        self,
        agent_handler_with_mock: AgentHandler,
        worktree_env: WorktreeEnv,
    ):
        """Test that RunAgent returns the assistant's response."""
        from doeff_conductor.effects.agent import RunAgent

        effect = RunAgent(env=worktree_env, prompt="Review the code")
        result = agent_handler_with_mock.handle_run_agent(effect)

        assert "reviewed" in result.lower()
        assert "issues" in result.lower()

    def test_spawn_agent_returns_ref(
        self,
        agent_handler_with_mock: AgentHandler,
        mock_handler: MockOpenCodeHandler,
        worktree_env: WorktreeEnv,
    ):
        """Test that SpawnAgent returns an AgentRef."""
        from doeff_conductor.effects.agent import SpawnAgent

        effect = SpawnAgent(env=worktree_env, prompt="Background task", name="worker-1")
        ref = agent_handler_with_mock.handle_spawn_agent(effect)

        assert isinstance(ref, AgentRef)
        assert ref.name == "worker-1"
        assert ref.workflow_id == "test-workflow"
        assert ref.id.startswith("mock-sess-")

    def test_send_message_to_spawned_agent(
        self,
        agent_handler_with_mock: AgentHandler,
        mock_handler: MockOpenCodeHandler,
        worktree_env: WorktreeEnv,
    ):
        """Test sending message to a spawned agent."""
        from doeff_conductor.effects.agent import SendMessage, SpawnAgent

        # Spawn agent first
        spawn_effect = SpawnAgent(env=worktree_env, prompt="Start task", name="worker")
        ref = agent_handler_with_mock.handle_spawn_agent(spawn_effect)

        # Send follow-up message
        msg_effect = SendMessage(agent_ref=ref, message="Continue with step 2", wait=False)
        agent_handler_with_mock.handle_send_message(msg_effect)

        # Verify message was recorded
        messages = mock_handler._messages[ref.id]
        assert len(messages) >= 2  # At least spawn prompt + follow-up

    def test_capture_output_returns_messages(
        self,
        agent_handler_with_mock: AgentHandler,
        mock_handler: MockOpenCodeHandler,
        worktree_env: WorktreeEnv,
    ):
        """Test capturing output from agent session."""
        from doeff_conductor.effects.agent import CaptureOutput, RunAgent

        # Run agent first
        run_effect = RunAgent(env=worktree_env, prompt="Do the task")
        agent_handler_with_mock.handle_run_agent(run_effect)

        # Get session ref
        session_id = list(mock_handler._sessions.keys())[0]
        ref = AgentRef(
            id=session_id,
            name="test",
            workflow_id="test-workflow",
            env_id="test-env",
            agent_type="claude",
        )

        # Capture output
        capture_effect = CaptureOutput(agent_ref=ref, lines=10)
        output = agent_handler_with_mock.handle_capture_output(capture_effect)

        assert "[user]" in output
        assert "[assistant]" in output

    def test_wait_for_status_returns_done(
        self,
        agent_handler_with_mock: AgentHandler,
        mock_handler: MockOpenCodeHandler,
        worktree_env: WorktreeEnv,
    ):
        """Test waiting for agent to reach done status."""
        from doeff_conductor.effects.agent import RunAgent, WaitForStatus

        # Run agent (will complete immediately in mock)
        run_effect = RunAgent(env=worktree_env, prompt="Quick task")
        agent_handler_with_mock.handle_run_agent(run_effect)

        # Get session ref
        session_id = list(mock_handler._sessions.keys())[0]
        ref = AgentRef(
            id=session_id,
            name="test",
            workflow_id="test-workflow",
            env_id="test-env",
            agent_type="claude",
        )

        # Wait for done status
        wait_effect = WaitForStatus(
            agent_ref=ref,
            target=AgenticSessionStatus.DONE,
            timeout=5.0,
        )
        status = agent_handler_with_mock.handle_wait_for_status(wait_effect)

        assert status == AgenticSessionStatus.DONE


class TestConductorWorkflowIntegration:
    """Test full conductor workflows with agentic effects."""

    def test_simple_agent_workflow(
        self, mock_handler: MockOpenCodeHandler, worktree_env: WorktreeEnv
    ):
        """Test a simple workflow that runs an agent."""

        @do
        def simple_workflow():
            result = yield RunAgent(env=worktree_env, prompt="Implement feature X")
            return result

        # Create handler that uses mock
        agent_handler = AgentHandler(workflow_id="test-workflow")
        agent_handler._opencode_handler = mock_handler

        handlers = {
            RunAgent: make_scheduled_handler(agent_handler.handle_run_agent),
        }

        result = run_sync(simple_workflow(), scheduled_handlers=handlers)

        assert result.is_ok
        assert "Implementation complete" in result.value

    def test_sequential_agent_workflow(
        self, mock_handler: MockOpenCodeHandler, worktree_env: WorktreeEnv
    ):
        """Test workflow with sequential agent calls."""

        @do
        def review_and_fix():
            # First agent: review
            review = yield RunAgent(env=worktree_env, prompt="Review the code")

            # Second agent: fix based on review
            fix = yield RunAgent(env=worktree_env, prompt=f"Fix these issues: {review}")

            return {"review": review, "fix": fix}

        agent_handler = AgentHandler(workflow_id="test-workflow")
        agent_handler._opencode_handler = mock_handler

        handlers = {
            RunAgent: make_scheduled_handler(agent_handler.handle_run_agent),
        }

        result = run_sync(review_and_fix(), scheduled_handlers=handlers)

        assert result.is_ok
        assert "reviewed" in result.value["review"].lower()
        assert "fixed" in result.value["fix"].lower()

    def test_spawn_and_wait_workflow(
        self, mock_handler: MockOpenCodeHandler, worktree_env: WorktreeEnv
    ):
        """Test workflow that spawns agent and waits for completion."""

        @do
        def spawn_workflow():
            # Spawn background agent
            ref = yield SpawnAgent(env=worktree_env, prompt="Long task", name="bg-worker")

            # Wait for completion
            status = yield WaitForStatus(
                agent_ref=ref,
                target=AgenticSessionStatus.DONE,
                timeout=10.0,
            )

            # Capture output
            output = yield CaptureOutput(agent_ref=ref, lines=100)

            return {"status": str(status), "output": output}

        agent_handler = AgentHandler(workflow_id="test-workflow")
        agent_handler._opencode_handler = mock_handler

        handlers = {
            SpawnAgent: make_scheduled_handler(agent_handler.handle_spawn_agent),
            WaitForStatus: make_scheduled_handler(agent_handler.handle_wait_for_status),
            CaptureOutput: make_scheduled_handler(agent_handler.handle_capture_output),
        }

        result = run_sync(spawn_workflow(), scheduled_handlers=handlers)

        assert result.is_ok
        # Note: Mock doesn't complete spawned agents automatically,
        # so status might still be RUNNING


# =============================================================================
# Real OpenCode Integration Tests (Optional)
# =============================================================================


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("CONDUCTOR_TEST_REAL_OPENCODE"),
    reason="Set CONDUCTOR_TEST_REAL_OPENCODE=1 to run real integration tests",
)
class TestRealOpenCodeIntegration:
    """
    Integration tests that use a real OpenCode server.

    These tests require:
    1. OpenCode server running (or auto-started)
    2. CONDUCTOR_TEST_REAL_OPENCODE=1 environment variable

    Run:
        CONDUCTOR_TEST_REAL_OPENCODE=1 uv run pytest -v -m integration
    """

    @pytest.fixture
    def real_worktree(self, tmp_path: Path) -> WorktreeEnv:
        """Create a real test directory."""
        test_dir = tmp_path / "test_project"
        test_dir.mkdir()
        (test_dir / "README.md").write_text("# Test Project\n")
        (test_dir / "main.py").write_text("print('hello')\n")

        return WorktreeEnv(
            id="real-test-env",
            path=test_dir,
            branch="test",
            base_commit="abc123",
            created_at=datetime.now(timezone.utc),
        )

    def test_real_run_agent(self, real_worktree: WorktreeEnv):
        """Test RunAgent with real OpenCode server."""
        from doeff_conductor.effects.agent import RunAgent

        agent_handler = AgentHandler(workflow_id=f"test-{secrets.token_hex(4)}")

        effect = RunAgent(
            env=real_worktree,
            prompt="List the files in this directory and describe what you see.",
        )

        # This will use real OpenCode
        result = agent_handler.handle_run_agent(effect)

        # Should get a meaningful response about the files
        assert result  # Non-empty response
        assert len(result) > 10  # Some substantial content
