"""Tests for doeff_agentic.types module."""

from datetime import datetime, timezone

from doeff_agentic.types import (
    AgentConfig,
    AgentInfo,
    AgentStatus,
    WatchEventType,
    WatchUpdate,
    WorkflowInfo,
    WorkflowStatus,
)


class TestAgentInfo:
    """Tests for AgentInfo."""

    def test_create_agent_info(self):
        """Test creating an AgentInfo."""
        agent = AgentInfo(
            name="test-agent",
            status=AgentStatus.RUNNING,
            session_name="doeff-abc1234-test-agent",
            pane_id="%42",
        )
        assert agent.name == "test-agent"
        assert agent.status == AgentStatus.RUNNING
        assert agent.session_name == "doeff-abc1234-test-agent"
        assert agent.pane_id == "%42"

    def test_agent_info_to_dict(self):
        """Test converting AgentInfo to dict."""
        now = datetime.now(timezone.utc)
        agent = AgentInfo(
            name="test-agent",
            status=AgentStatus.BLOCKED,
            session_name="doeff-abc1234-test-agent",
            started_at=now,
        )
        d = agent.to_dict()
        assert d["name"] == "test-agent"
        assert d["status"] == "blocked"
        assert d["session_name"] == "doeff-abc1234-test-agent"
        assert d["started_at"] == now.isoformat()

    def test_agent_info_from_dict(self):
        """Test creating AgentInfo from dict."""
        d = {
            "name": "test-agent",
            "status": "running",
            "session_name": "doeff-abc1234-test-agent",
            "pane_id": "%42",
            "started_at": "2025-01-01T12:00:00+00:00",
        }
        agent = AgentInfo.from_dict(d)
        assert agent.name == "test-agent"
        assert agent.status == AgentStatus.RUNNING
        assert agent.pane_id == "%42"


class TestWorkflowInfo:
    """Tests for WorkflowInfo."""

    def test_create_workflow_info(self):
        """Test creating a WorkflowInfo."""
        now = datetime.now(timezone.utc)
        workflow = WorkflowInfo(
            id="abc1234",
            name="test-workflow",
            status=WorkflowStatus.RUNNING,
            started_at=now,
            updated_at=now,
        )
        assert workflow.id == "abc1234"
        assert workflow.name == "test-workflow"
        assert workflow.status == WorkflowStatus.RUNNING

    def test_workflow_info_with_agents(self):
        """Test WorkflowInfo with nested agents."""
        now = datetime.now(timezone.utc)
        agents = (
            AgentInfo(
                name="agent-1",
                status=AgentStatus.DONE,
                session_name="doeff-abc1234-agent-1",
                started_at=now,
            ),
            AgentInfo(
                name="agent-2",
                status=AgentStatus.RUNNING,
                session_name="doeff-abc1234-agent-2",
                started_at=now,
            ),
        )
        workflow = WorkflowInfo(
            id="abc1234",
            name="test-workflow",
            status=WorkflowStatus.RUNNING,
            started_at=now,
            updated_at=now,
            current_agent="agent-2",
            agents=agents,
        )
        assert len(workflow.agents) == 2
        assert workflow.current_agent == "agent-2"

    def test_workflow_info_to_dict(self):
        """Test converting WorkflowInfo to dict."""
        now = datetime.now(timezone.utc)
        workflow = WorkflowInfo(
            id="abc1234",
            name="test-workflow",
            status=WorkflowStatus.BLOCKED,
            started_at=now,
            updated_at=now,
            last_slog={"status": "waiting", "msg": "test"},
        )
        d = workflow.to_dict()
        assert d["id"] == "abc1234"
        assert d["status"] == "blocked"
        assert d["last_slog"] == {"status": "waiting", "msg": "test"}

    def test_workflow_info_from_dict(self):
        """Test creating WorkflowInfo from dict."""
        d = {
            "id": "abc1234",
            "name": "test-workflow",
            "status": "completed",
            "started_at": "2025-01-01T12:00:00+00:00",
            "updated_at": "2025-01-01T13:00:00+00:00",
            "agents": [],
        }
        workflow = WorkflowInfo.from_dict(d)
        assert workflow.id == "abc1234"
        assert workflow.status == WorkflowStatus.COMPLETED


class TestWatchUpdate:
    """Tests for WatchUpdate."""

    def test_create_watch_update(self):
        """Test creating a WatchUpdate."""
        now = datetime.now(timezone.utc)
        workflow = WorkflowInfo(
            id="abc1234",
            name="test-workflow",
            status=WorkflowStatus.RUNNING,
            started_at=now,
            updated_at=now,
        )
        update = WatchUpdate(
            workflow=workflow,
            event=WatchEventType.STATUS_CHANGE,
            data={"old": "pending", "new": "running"},
        )
        assert update.event == WatchEventType.STATUS_CHANGE
        assert update.data["old"] == "pending"

    def test_watch_update_to_dict(self):
        """Test converting WatchUpdate to dict."""
        now = datetime.now(timezone.utc)
        workflow = WorkflowInfo(
            id="abc1234",
            name="test-workflow",
            status=WorkflowStatus.RUNNING,
            started_at=now,
            updated_at=now,
        )
        update = WatchUpdate(
            workflow=workflow,
            event=WatchEventType.SLOG,
            data={"status": "processing"},
        )
        d = update.to_dict()
        assert d["event"] == "slog"
        assert d["data"]["status"] == "processing"


class TestAgentConfig:
    """Tests for AgentConfig."""

    def test_create_agent_config(self):
        """Test creating an AgentConfig."""
        config = AgentConfig(
            agent_type="claude",
            prompt="Test prompt",
            profile="code-review",
        )
        assert config.agent_type == "claude"
        assert config.prompt == "Test prompt"
        assert config.profile == "code-review"
        assert config.resume is False

    def test_agent_config_to_dict(self):
        """Test converting AgentConfig to dict."""
        config = AgentConfig(
            agent_type="codex",
            prompt="Test",
            work_dir="/tmp/test",
        )
        d = config.to_dict()
        assert d["agent_type"] == "codex"
        assert d["work_dir"] == "/tmp/test"
