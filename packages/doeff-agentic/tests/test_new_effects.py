"""Tests for new spec-compliant effects in doeff_agentic.effects module."""

import pytest
from doeff_agentic.effects import (
    AgenticAbortSession,
    # Environment effects
    AgenticCreateEnvironment,
    # Session effects
    AgenticCreateSession,
    # Workflow effects
    AgenticCreateWorkflow,
    AgenticDeleteEnvironment,
    AgenticDeleteSession,
    AgenticForkSession,
    # Parallel effects
    AgenticGather,
    AgenticGetEnvironment,
    AgenticGetMessages,
    AgenticGetSession,
    # Status effects
    AgenticGetSessionStatus,
    AgenticGetWorkflow,
    # Event effects
    AgenticNextEvent,
    AgenticRace,
    # Message effects
    AgenticSendMessage,
    AgenticSupportsCapability,
)
from doeff_agentic.types import AgenticEnvironmentType


class TestWorkflowEffects:
    """Tests for workflow effects."""

    def test_create_workflow_defaults(self):
        """Test AgenticCreateWorkflow with defaults."""
        effect = AgenticCreateWorkflow()
        assert effect.name is None
        assert effect.metadata is None

    def test_create_workflow_with_name(self):
        """Test AgenticCreateWorkflow with name."""
        effect = AgenticCreateWorkflow(name="PR Review", metadata={"pr": 123})
        assert effect.name == "PR Review"
        assert effect.metadata == {"pr": 123}

    def test_get_workflow(self):
        """Test AgenticGetWorkflow."""
        effect = AgenticGetWorkflow()
        assert effect.created_at is None


class TestEnvironmentEffects:
    """Tests for environment effects."""

    def test_create_environment_worktree(self):
        """Test creating worktree environment."""
        effect = AgenticCreateEnvironment(
            env_type=AgenticEnvironmentType.WORKTREE,
            name="pr-review",
            base_commit="main",
        )
        assert effect.env_type == AgenticEnvironmentType.WORKTREE
        assert effect.base_commit == "main"

    def test_create_environment_inherited(self):
        """Test creating inherited environment."""
        effect = AgenticCreateEnvironment(
            env_type=AgenticEnvironmentType.INHERITED,
            source_environment_id="env-abc123",
        )
        assert effect.env_type == AgenticEnvironmentType.INHERITED
        assert effect.source_environment_id == "env-abc123"

    def test_create_environment_shared(self):
        """Test creating shared environment."""
        effect = AgenticCreateEnvironment(
            env_type=AgenticEnvironmentType.SHARED,
            working_dir="/path/to/project",
        )
        assert effect.env_type == AgenticEnvironmentType.SHARED
        assert effect.working_dir == "/path/to/project"

    def test_get_environment(self):
        """Test getting environment by ID."""
        effect = AgenticGetEnvironment(environment_id="env-abc123")
        assert effect.environment_id == "env-abc123"

    def test_delete_environment(self):
        """Test deleting environment."""
        effect = AgenticDeleteEnvironment(environment_id="env-abc123", force=True)
        assert effect.environment_id == "env-abc123"
        assert effect.force is True

    def test_delete_environment_defaults(self):
        """Test delete environment defaults."""
        effect = AgenticDeleteEnvironment(environment_id="env-abc123")
        assert effect.force is False


class TestSessionEffects:
    """Tests for session effects."""

    def test_create_session_minimal(self):
        """Test creating session with minimum params."""
        effect = AgenticCreateSession(name="reviewer")
        assert effect.name == "reviewer"
        assert effect.environment_id is None
        assert effect.title is None
        assert effect.agent is None
        assert effect.model is None

    def test_create_session_full(self):
        """Test creating session with all params."""
        effect = AgenticCreateSession(
            name="reviewer",
            environment_id="env-abc",
            title="Code Reviewer",
            agent="code-review",
            model="claude-sonnet-4-20250514",
        )
        assert effect.name == "reviewer"
        assert effect.environment_id == "env-abc"
        assert effect.title == "Code Reviewer"
        assert effect.agent == "code-review"
        assert effect.model == "claude-sonnet-4-20250514"

    def test_fork_session(self):
        """Test forking a session."""
        effect = AgenticForkSession(
            session_id="sess_abc123",
            name="forked-session",
            message_id="msg_xyz",
        )
        assert effect.session_id == "sess_abc123"
        assert effect.name == "forked-session"
        assert effect.message_id == "msg_xyz"

    def test_fork_session_latest(self):
        """Test forking at latest message."""
        effect = AgenticForkSession(
            session_id="sess_abc123",
            name="forked",
        )
        assert effect.message_id is None

    def test_get_session_by_id(self):
        """Test getting session by ID."""
        effect = AgenticGetSession(session_id="sess_abc123")
        assert effect.session_id == "sess_abc123"
        assert effect.name is None

    def test_get_session_by_name(self):
        """Test getting session by name."""
        effect = AgenticGetSession(name="reviewer")
        assert effect.session_id is None
        assert effect.name == "reviewer"

    def test_abort_session(self):
        """Test aborting session."""
        effect = AgenticAbortSession(session_id="sess_abc123")
        assert effect.session_id == "sess_abc123"

    def test_delete_session(self):
        """Test deleting session."""
        effect = AgenticDeleteSession(session_id="sess_abc123")
        assert effect.session_id == "sess_abc123"


class TestMessageEffects:
    """Tests for message effects."""

    def test_send_message_minimal(self):
        """Test sending message with minimal params."""
        effect = AgenticSendMessage(
            session_id="sess_abc",
            content="Review this code",
        )
        assert effect.session_id == "sess_abc"
        assert effect.content == "Review this code"
        assert effect.wait is False
        assert effect.agent is None
        assert effect.model is None

    def test_send_message_wait(self):
        """Test sending message with wait."""
        effect = AgenticSendMessage(
            session_id="sess_abc",
            content="Review this code",
            wait=True,
        )
        assert effect.wait is True

    def test_send_message_overrides(self):
        """Test sending message with agent/model override."""
        effect = AgenticSendMessage(
            session_id="sess_abc",
            content="Quick fix",
            agent="quick-fix",
            model="gpt-4o",
        )
        assert effect.agent == "quick-fix"
        assert effect.model == "gpt-4o"

    def test_get_messages(self):
        """Test getting messages."""
        effect = AgenticGetMessages(session_id="sess_abc", limit=10)
        assert effect.session_id == "sess_abc"
        assert effect.limit == 10

    def test_get_messages_all(self):
        """Test getting all messages."""
        effect = AgenticGetMessages(session_id="sess_abc")
        assert effect.limit is None


class TestEventEffects:
    """Tests for event effects."""

    def test_next_event_no_timeout(self):
        """Test waiting for next event without timeout."""
        effect = AgenticNextEvent(session_id="sess_abc")
        assert effect.session_id == "sess_abc"
        assert effect.timeout is None

    def test_next_event_with_timeout(self):
        """Test waiting for next event with timeout."""
        effect = AgenticNextEvent(session_id="sess_abc", timeout=30.0)
        assert effect.timeout == 30.0


class TestParallelEffects:
    """Tests for parallel execution effects."""

    def test_gather(self):
        """Test gathering multiple sessions."""
        effect = AgenticGather(
            session_names=("reviewer", "fixer", "tester"),
            timeout=300.0,
        )
        assert effect.session_names == ("reviewer", "fixer", "tester")
        assert effect.timeout == 300.0

    def test_gather_no_timeout(self):
        """Test gather without timeout."""
        effect = AgenticGather(session_names=("a", "b"))
        assert effect.timeout is None

    def test_race(self):
        """Test racing multiple sessions."""
        effect = AgenticRace(
            session_names=("fast", "slow"),
            timeout=60.0,
        )
        assert effect.session_names == ("fast", "slow")
        assert effect.timeout == 60.0


class TestStatusEffects:
    """Tests for status effects."""

    def test_get_session_status(self):
        """Test getting session status."""
        effect = AgenticGetSessionStatus(session_id="sess_abc")
        assert effect.session_id == "sess_abc"

    def test_supports_capability(self):
        """Test checking capability."""
        effect = AgenticSupportsCapability(capability="fork")
        assert effect.capability == "fork"

    def test_supports_capability_various(self):
        """Test various capabilities."""
        for cap in ["fork", "events", "worktree", "container"]:
            effect = AgenticSupportsCapability(capability=cap)
            assert effect.capability == cap


class TestEffectFrozen:
    """Tests that effects are frozen (immutable)."""

    def test_create_session_frozen(self):
        """Test that AgenticCreateSession is frozen."""
        effect = AgenticCreateSession(name="test")
        with pytest.raises(AttributeError):
            effect.name = "changed"  # type: ignore

    def test_send_message_frozen(self):
        """Test that AgenticSendMessage is frozen."""
        effect = AgenticSendMessage(session_id="sess", content="test")
        with pytest.raises(AttributeError):
            effect.content = "changed"  # type: ignore

    def test_create_environment_frozen(self):
        """Test that AgenticCreateEnvironment is frozen."""
        effect = AgenticCreateEnvironment(env_type=AgenticEnvironmentType.SHARED)
        with pytest.raises(AttributeError):
            effect.env_type = AgenticEnvironmentType.WORKTREE  # type: ignore
