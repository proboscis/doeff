"""
End-to-end tests for conductor → agentic → OpenCode → agent pipeline.

These tests exercise the integration between doeff-conductor and doeff-agentic,
verifying that conductor's agent effects correctly work with the agentic layer.

Test Categories:
1. Mock Agentic Tests - Mock at the doeff-agentic handler level (fast CI tests)
2. Real OpenCode E2E Tests - Require actual OpenCode server (optional)

Run Configuration:
- Default: Only mock agentic tests run
- With CONDUCTOR_E2E=1: All E2E tests run (including real OpenCode)

Example:
    # Run all tests including E2E
    CONDUCTOR_E2E=1 pytest packages/doeff-conductor/tests/test_agent_e2e.py
    
    # Run only mock tests
    pytest packages/doeff-conductor/tests/test_agent_e2e.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# =============================================================================
# Mock Agentic Handler Factory
# =============================================================================


def create_mock_agentic_handler():
    """Create a mock OpenCodeHandler that simulates agentic behavior.
    
    This mocks at the agentic level (handler methods), not HTTP level.
    The mock provides realistic responses for conductor's agent effects.
    """
    # Import all types at the top so they're available to all nested functions
    from doeff_agentic import (
        AgenticCreateEnvironment,
        AgenticCreateSession,
        AgenticEnvironmentHandle,
        AgenticGetMessages,
        AgenticGetSessionStatus,
        AgenticMessage,
        AgenticMessageHandle,
        AgenticSendMessage,
        AgenticSessionHandle,
        AgenticSessionStatus,
    )

    mock = MagicMock()

    # Track state
    mock._sessions = {}
    mock._session_counter = 0
    mock._env_counter = 0
    mock._messages = {}

    def handle_create_environment(effect):
        mock._env_counter += 1
        env_id = f"env-{mock._env_counter:04d}"

        return AgenticEnvironmentHandle(
            id=env_id,
            env_type=effect.env_type,
            name=effect.name,
            working_dir=effect.working_dir or "/tmp/mock",
            created_at=datetime.now(timezone.utc),
        )

    def handle_create_session(effect):
        mock._session_counter += 1
        session_id = f"session-{mock._session_counter:04d}"

        session = AgenticSessionHandle(
            id=session_id,
            name=effect.name,
            workflow_id="mock-workflow",
            environment_id=effect.environment_id or "env-0001",
            status=AgenticSessionStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            title=effect.title or effect.name,
        )
        mock._sessions[session_id] = session
        mock._messages[session_id] = []
        return session

    def handle_send_message(effect):
        # Add user message
        mock._messages[effect.session_id].append({
            "role": "user",
            "content": effect.content,
        })

        # Auto-generate assistant response
        mock._messages[effect.session_id].append({
            "role": "assistant",
            "content": f"[Mock response to: {effect.content[:50]}...]",
        })

        # Update session status to done
        if effect.session_id in mock._sessions:
            session = mock._sessions[effect.session_id]
            mock._sessions[effect.session_id] = AgenticSessionHandle(
                id=session.id,
                name=session.name,
                workflow_id=session.workflow_id,
                environment_id=session.environment_id,
                status=AgenticSessionStatus.DONE,
                created_at=session.created_at,
                title=session.title,
            )

        return AgenticMessageHandle(
            id=f"msg-{len(mock._messages[effect.session_id])}",
            session_id=effect.session_id,
            role="user",
            created_at=datetime.now(timezone.utc),
        )

    def handle_get_messages(effect):
        messages = mock._messages.get(effect.session_id, [])
        return [
            AgenticMessage(
                id=f"msg-{i}",
                session_id=effect.session_id,
                role=msg["role"],
                content=msg["content"],
                created_at=datetime.now(timezone.utc),
            )
            for i, msg in enumerate(messages)
        ]

    def handle_get_session_status(effect):
        if effect.session_id in mock._sessions:
            return mock._sessions[effect.session_id].status
        return AgenticSessionStatus.PENDING

    def dispatch_effect(effect):
        """Route effect to appropriate handler."""
        if isinstance(effect, AgenticCreateEnvironment):
            return handle_create_environment(effect)
        if isinstance(effect, AgenticCreateSession):
            return handle_create_session(effect)
        if isinstance(effect, AgenticSendMessage):
            return handle_send_message(effect)
        if isinstance(effect, AgenticGetMessages):
            return handle_get_messages(effect)
        if isinstance(effect, AgenticGetSessionStatus):
            return handle_get_session_status(effect)
        raise ValueError(f"Unknown effect type: {type(effect)}")

    # Assign specific handler methods (new interface)
    mock.handle_create_environment = MagicMock(side_effect=handle_create_environment)
    mock.handle_create_session = MagicMock(side_effect=handle_create_session)
    mock.handle_send_message = MagicMock(side_effect=handle_send_message)
    mock.handle_get_messages = MagicMock(side_effect=handle_get_messages)
    mock.handle_get_session_status = MagicMock(side_effect=handle_get_session_status)

    # Keep legacy handle method for backwards compatibility
    mock.handle = MagicMock(side_effect=lambda effect: dispatch_effect(effect))

    return mock


# =============================================================================
# Mock Agentic Tests
# =============================================================================


class TestAgentHandlerWithMockAgentic:
    """Tests for AgentHandler using mocked doeff-agentic."""

    @pytest.fixture
    def agent_handler(self):
        """Create AgentHandler with mocked agentic handler."""
        from doeff_conductor.handlers.agent_handler import AgentHandler

        handler = AgentHandler(workflow_id="test-workflow")
        handler._opencode_handler = create_mock_agentic_handler()
        return handler

    @pytest.fixture
    def worktree_env(self, tmp_path: Path):
        """Create a test WorktreeEnv."""
        from doeff_conductor.types import WorktreeEnv

        return WorktreeEnv(
            id="test-env",
            path=tmp_path,
            branch="test-branch",
            base_commit="abc123",
            created_at=datetime.now(timezone.utc),
        )

    def test_run_agent_returns_output(self, agent_handler, worktree_env):
        """Test RunAgent effect returns assistant output."""
        from doeff_conductor.effects.agent import RunAgent

        effect = RunAgent(
            env=worktree_env,
            prompt="Write a hello world program",
        )

        result = agent_handler.handle_run_agent(effect)

        assert result is not None
        assert "Mock response" in result

    def test_spawn_agent_returns_ref(self, agent_handler, worktree_env):
        """Test SpawnAgent returns valid AgentRef."""
        from doeff_conductor.effects.agent import SpawnAgent
        from doeff_conductor.types import AgentRef

        effect = SpawnAgent(
            env=worktree_env,
            prompt="Background task",
            name="bg-agent",
        )

        ref = agent_handler.handle_spawn_agent(effect)

        assert isinstance(ref, AgentRef)
        assert ref.name == "bg-agent"
        assert ref.workflow_id == "test-workflow"

    def test_capture_output_gets_messages(self, agent_handler, worktree_env):
        """Test CaptureOutput retrieves session messages."""
        from doeff_conductor.effects.agent import CaptureOutput, SpawnAgent

        # Spawn an agent first
        spawn_effect = SpawnAgent(
            env=worktree_env,
            prompt="Task prompt",
            name="output-agent",
        )
        ref = agent_handler.handle_spawn_agent(spawn_effect)

        # Capture output
        capture_effect = CaptureOutput(agent_ref=ref, lines=100)
        output = agent_handler.handle_capture_output(capture_effect)

        # Should have the initial prompt and mock response
        assert "Task prompt" in output or "assistant" in output


class TestConductorWorkflowWithMockAgentic:
    """Tests for full conductor workflows using mocked doeff-agentic."""

    def test_basic_agent_workflow(
        self,
        test_repo: Path,
        worktree_base: Path,
        issues_dir: Path,
    ):
        """Test a basic workflow: issue -> agent -> changes."""
        from doeff_conductor import (
            CreateIssue,
            CreateWorktree,
            DeleteWorktree,
            IssueHandler,
            IssueStatus,
            ResolveIssue,
            WorktreeHandler,
            make_scheduled_handler,
        )
        from doeff_conductor.effects.agent import RunAgent
        from doeff_conductor.handlers import run_sync
        from doeff_conductor.handlers.agent_handler import AgentHandler

        from doeff import do

        @do
        def agent_workflow():
            # Create issue
            issue = yield CreateIssue(
                title="Add hello module",
                body="Create hello.py with greeting function",
                labels=("feature",),
            )

            # Create worktree
            env = yield CreateWorktree(issue=issue, suffix="impl")

            # Run agent (mocked)
            output = yield RunAgent(env=env, prompt=issue.body)

            # Simulate what agent would do
            (env.path / "hello.py").write_text('def greet():\n    return "Hello!"\n')

            # Resolve issue
            resolved = yield ResolveIssue(
                issue=issue,
                result=f"Agent output: {output[:50]}...",
            )

            # Cleanup
            yield DeleteWorktree(env=env, force=True)

            return {
                "issue_id": issue.id,
                "resolved": resolved.status == IssueStatus.RESOLVED,
                "agent_output": output,
            }

        # Set up handlers
        worktree_handler = WorktreeHandler(repo_path=test_repo)
        worktree_handler.worktree_base = worktree_base
        issue_handler = IssueHandler(issues_dir=issues_dir)

        # Agent handler with mocked agentic
        agent_handler = AgentHandler(workflow_id="test")
        agent_handler._opencode_handler = create_mock_agentic_handler()

        handlers = {
            CreateIssue: make_scheduled_handler(issue_handler.handle_create_issue),
            ResolveIssue: make_scheduled_handler(issue_handler.handle_resolve_issue),
            CreateWorktree: make_scheduled_handler(worktree_handler.handle_create_worktree),
            DeleteWorktree: make_scheduled_handler(worktree_handler.handle_delete_worktree),
            RunAgent: make_scheduled_handler(agent_handler.handle_run_agent),
        }

        result = run_sync(agent_workflow(), scheduled_handlers=handlers)

        assert result.is_ok
        workflow_result = result.value
        assert workflow_result["resolved"] is True
        assert "Mock response" in workflow_result["agent_output"]

    def test_sequential_agents_chain_output(
        self,
        test_repo: Path,
        worktree_base: Path,
    ):
        """Test that output from agent 1 feeds into agent 2."""
        from doeff_conductor import (
            CreateWorktree,
            DeleteWorktree,
            WorktreeHandler,
            make_scheduled_handler,
        )
        from doeff_conductor.effects.agent import RunAgent
        from doeff_conductor.handlers import run_sync
        from doeff_conductor.handlers.agent_handler import AgentHandler

        from doeff import do

        @do
        def sequential_agents():
            # Create worktree
            env = yield CreateWorktree(suffix="chain")

            # First agent
            output1 = yield RunAgent(
                env=env,
                prompt="Generate a list of features",
                name="feature-agent",
            )

            # Second agent uses first agent's output
            output2 = yield RunAgent(
                env=env,
                prompt=f"Implement these features: {output1}",
                name="impl-agent",
            )

            # Cleanup
            yield DeleteWorktree(env=env, force=True)

            return {"output1": output1, "output2": output2}

        # Set up handlers
        worktree_handler = WorktreeHandler(repo_path=test_repo)
        worktree_handler.worktree_base = worktree_base

        # Agent handler with mocked agentic
        agent_handler = AgentHandler(workflow_id="chain-test")
        agent_handler._opencode_handler = create_mock_agentic_handler()

        handlers = {
            CreateWorktree: make_scheduled_handler(worktree_handler.handle_create_worktree),
            DeleteWorktree: make_scheduled_handler(worktree_handler.handle_delete_worktree),
            RunAgent: make_scheduled_handler(agent_handler.handle_run_agent),
        }

        result = run_sync(sequential_agents(), scheduled_handlers=handlers)

        assert result.is_ok
        outputs = result.value

        # Both agents should have produced output
        assert outputs["output1"] is not None
        assert outputs["output2"] is not None
        assert "Mock response" in outputs["output1"]
        assert "Mock response" in outputs["output2"]

    def test_parallel_agents_no_state_leakage(
        self,
        test_repo: Path,
        worktree_base: Path,
    ):
        """Test that parallel agents don't leak state between sessions."""
        from doeff_conductor import (
            CreateWorktree,
            DeleteWorktree,
            WorktreeHandler,
            make_scheduled_handler,
        )
        from doeff_conductor.effects.agent import CaptureOutput, SpawnAgent
        from doeff_conductor.handlers import run_sync
        from doeff_conductor.handlers.agent_handler import AgentHandler

        from doeff import do

        @do
        def parallel_agents():
            # Create two worktrees
            env1 = yield CreateWorktree(suffix="agent1")
            env2 = yield CreateWorktree(suffix="agent2")

            # Spawn agents with different prompts
            ref1 = yield SpawnAgent(
                env=env1,
                prompt="Task for agent ONE: unique-marker-alpha",
                name="agent-one",
            )
            ref2 = yield SpawnAgent(
                env=env2,
                prompt="Task for agent TWO: unique-marker-beta",
                name="agent-two",
            )

            # Capture outputs
            output1 = yield CaptureOutput(agent_ref=ref1, lines=100)
            output2 = yield CaptureOutput(agent_ref=ref2, lines=100)

            # Cleanup
            yield DeleteWorktree(env=env1, force=True)
            yield DeleteWorktree(env=env2, force=True)

            return {
                "ref1_id": ref1.id,
                "ref2_id": ref2.id,
                "output1": output1,
                "output2": output2,
            }

        # Set up handlers
        worktree_handler = WorktreeHandler(repo_path=test_repo)
        worktree_handler.worktree_base = worktree_base

        # Agent handler with mocked agentic
        agent_handler = AgentHandler(workflow_id="parallel-test")
        agent_handler._opencode_handler = create_mock_agentic_handler()

        handlers = {
            CreateWorktree: make_scheduled_handler(worktree_handler.handle_create_worktree),
            DeleteWorktree: make_scheduled_handler(worktree_handler.handle_delete_worktree),
            SpawnAgent: make_scheduled_handler(agent_handler.handle_spawn_agent),
            CaptureOutput: make_scheduled_handler(agent_handler.handle_capture_output),
        }

        result = run_sync(parallel_agents(), scheduled_handlers=handlers)

        assert result.is_ok
        outputs = result.value

        # Agents should have different session IDs
        assert outputs["ref1_id"] != outputs["ref2_id"]

        # Each output should contain its own prompt marker, not the other's
        assert "unique-marker-alpha" in outputs["output1"]
        assert "unique-marker-beta" in outputs["output2"]
        # Cross-contamination check
        assert "unique-marker-beta" not in outputs["output1"]
        assert "unique-marker-alpha" not in outputs["output2"]


# =============================================================================
# Agent Error Handling Tests
# =============================================================================


class TestAgentErrorHandling:
    """Tests for agent timeout and error recovery."""

    def test_agent_timeout_handling(self):
        """Test that agent timeout is handled gracefully."""
        from unittest.mock import MagicMock

        from doeff_agentic import AgenticSessionStatus
        from doeff_conductor.effects.agent import WaitForStatus
        from doeff_conductor.exceptions import AgentTimeoutError
        from doeff_conductor.handlers.agent_handler import AgentHandler
        from doeff_conductor.types import AgentRef

        # Create agent handler with mock that never returns terminal status
        handler = AgentHandler(workflow_id="timeout-test")
        mock_handler = MagicMock()
        mock_status = MagicMock()
        mock_status.is_terminal.return_value = False  # Never terminal
        # handle_get_session_status is called in the poll loop
        mock_handler.handle_get_session_status.return_value = mock_status
        handler._opencode_handler = mock_handler

        session_id = "mock-session-001"

        # Create agent ref
        agent_ref = AgentRef(
            id=session_id,
            name="timeout-agent",
            workflow_id="timeout-test",
            env_id="env-1",
            agent_type="opencode",
        )

        # Try to wait with very short timeout
        effect = WaitForStatus(
            agent_ref=agent_ref,
            target=AgenticSessionStatus.DONE,
            timeout=0.5,
            poll_interval=0.1,
        )

        with pytest.raises(AgentTimeoutError) as exc_info:
            handler.handle_wait_for_status(effect)

        assert exc_info.value.agent_id == session_id
        assert exc_info.value.timeout == 0.5

    def test_agent_error_recovery(self):
        """Test workflow handles agent errors gracefully."""
        import tempfile
        from datetime import datetime, timezone
        from unittest.mock import MagicMock

        from doeff_conductor.effects.agent import RunAgent
        from doeff_conductor.handlers.agent_handler import AgentHandler
        from doeff_conductor.types import WorktreeEnv

        # Create agent handler with mock that raises error
        handler = AgentHandler(workflow_id="error-test")
        mock_handler = MagicMock()
        # handle_create_environment is called first in handle_run_agent
        mock_handler.handle_create_environment.side_effect = Exception("Simulated agent failure")
        handler._opencode_handler = mock_handler

        # Create dummy env
        with tempfile.TemporaryDirectory() as tmp:
            worktree_env = WorktreeEnv(
                id="test-env",
                path=Path(tmp),
                branch="test-branch",
                base_commit="abc123",
                created_at=datetime.now(timezone.utc),
            )

            effect = RunAgent(env=worktree_env, prompt="Test")

            # Should raise the simulated error
            with pytest.raises(Exception) as exc_info:
                handler.handle_run_agent(effect)

            assert "Simulated agent failure" in str(exc_info.value)


# =============================================================================
# Real OpenCode E2E Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.requires_opencode
class TestRealAgentE2E:
    """E2E tests requiring a running OpenCode server.
    
    These tests verify the full conductor → agentic → OpenCode → agent pipeline
    with a real OpenCode server and actual agent execution.
    
    To run these tests:
        CONDUCTOR_E2E=1 pytest -m requires_opencode
    """

    @pytest.fixture
    def real_agent_handler(self, opencode_url: str | None):
        """Create AgentHandler connected to real OpenCode."""
        if opencode_url is None:
            pytest.skip("OpenCode not available")

        from doeff_agentic import OpenCodeHandler
        from doeff_conductor.handlers.agent_handler import AgentHandler

        # Create wrapper for OpenCodeHandler that provides .handle() interface
        real_handler = OpenCodeHandler(server_url=opencode_url)
        real_handler.initialize()

        from doeff_agentic import (
            AgenticCreateEnvironment,
            AgenticCreateSession,
            AgenticGetMessages,
            AgenticGetSessionStatus,
            AgenticSendMessage,
        )

        class HandlerWrapper:
            def __init__(self, handler):
                self._handler = handler

            def handle(self, effect):
                if isinstance(effect, AgenticCreateEnvironment):
                    return self._handler.handle_create_environment(effect)
                if isinstance(effect, AgenticCreateSession):
                    return self._handler.handle_create_session(effect)
                if isinstance(effect, AgenticSendMessage):
                    return self._handler.handle_send_message(effect)
                if isinstance(effect, AgenticGetMessages):
                    return self._handler.handle_get_messages(effect)
                if isinstance(effect, AgenticGetSessionStatus):
                    return self._handler.handle_get_session_status(effect)
                raise ValueError(f"Unknown effect type: {type(effect)}")

        handler = AgentHandler(workflow_id="e2e-test")
        handler._opencode_handler = HandlerWrapper(real_handler)
        yield handler
        real_handler.close()

    def test_agent_creates_file(self, test_repo: Path, worktree_base: Path, real_agent_handler):
        """Test that agent actually creates a file in worktree."""
        from doeff_conductor import (
            CreateWorktree,
            DeleteWorktree,
            WorktreeHandler,
            make_scheduled_handler,
        )
        from doeff_conductor.effects.agent import RunAgent
        from doeff_conductor.handlers import run_sync

        from doeff import do

        @do
        def file_creation_workflow():
            env = yield CreateWorktree(suffix="file-test")

            output = yield RunAgent(
                env=env,
                prompt="Create a file named 'test_file.txt' with content 'Hello from agent'",
                timeout=60.0,
            )

            test_file = env.path / "test_file.txt"
            file_exists = test_file.exists()
            file_content = test_file.read_text() if file_exists else ""

            yield DeleteWorktree(env=env, force=True)

            return {
                "file_exists": file_exists,
                "file_content": file_content,
                "agent_output": output,
            }

        worktree_handler = WorktreeHandler(repo_path=test_repo)
        worktree_handler.worktree_base = worktree_base

        handlers = {
            CreateWorktree: make_scheduled_handler(worktree_handler.handle_create_worktree),
            DeleteWorktree: make_scheduled_handler(worktree_handler.handle_delete_worktree),
            RunAgent: make_scheduled_handler(real_agent_handler.handle_run_agent),
        }

        result = run_sync(file_creation_workflow(), scheduled_handlers=handlers)

        if result.is_err:
            pytest.fail(f"Workflow failed: {result.result.error}")

        workflow_result = result.value
        assert workflow_result["file_exists"], "Agent should have created the test file"

    def test_spawn_and_capture_output(self, test_repo: Path, worktree_base: Path, real_agent_handler):
        """Test spawning a background agent and capturing its output."""
        from doeff_agentic import AgenticSessionStatus
        from doeff_conductor import (
            CreateWorktree,
            DeleteWorktree,
            WorktreeHandler,
            make_scheduled_handler,
        )
        from doeff_conductor.effects.agent import CaptureOutput, SpawnAgent, WaitForStatus
        from doeff_conductor.handlers import run_sync

        from doeff import do

        @do
        def spawn_capture_workflow():
            env = yield CreateWorktree(suffix="spawn-test")

            ref = yield SpawnAgent(
                env=env,
                prompt="List all files in the current directory",
                name="list-agent",
            )

            final_status = yield WaitForStatus(
                agent_ref=ref,
                target=(AgenticSessionStatus.DONE, AgenticSessionStatus.ERROR),
                timeout=60.0,
            )

            output = yield CaptureOutput(agent_ref=ref, lines=100)

            yield DeleteWorktree(env=env, force=True)

            return {
                "status": str(final_status),
                "output": output,
            }

        worktree_handler = WorktreeHandler(repo_path=test_repo)
        worktree_handler.worktree_base = worktree_base

        handlers = {
            CreateWorktree: make_scheduled_handler(worktree_handler.handle_create_worktree),
            DeleteWorktree: make_scheduled_handler(worktree_handler.handle_delete_worktree),
            SpawnAgent: make_scheduled_handler(real_agent_handler.handle_spawn_agent),
            WaitForStatus: make_scheduled_handler(real_agent_handler.handle_wait_for_status),
            CaptureOutput: make_scheduled_handler(real_agent_handler.handle_capture_output),
        }

        result = run_sync(spawn_capture_workflow(), scheduled_handlers=handlers)

        if result.is_err:
            pytest.fail(f"Workflow failed: {result.result.error}")

        workflow_result = result.value
        assert "DONE" in workflow_result["status"] or "ERROR" in workflow_result["status"]
        assert len(workflow_result["output"]) > 0


__all__ = [
    "TestAgentErrorHandling",
    "TestAgentHandlerWithMockAgentic",
    "TestConductorWorkflowWithMockAgentic",
    "TestRealAgentE2E",
]
