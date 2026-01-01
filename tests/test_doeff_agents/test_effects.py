"""Tests for agent effects."""

import pytest
from pathlib import Path
from datetime import datetime, timezone

from doeff_agents.effects import (
    # Types
    SessionHandle,
    Observation,
    # Effects
    LaunchEffect,
    MonitorEffect,
    CaptureEffect,
    SendEffect,
    StopEffect,
    SleepEffect,
    # Constructors
    Launch,
    Monitor,
    Capture,
    Send,
    Stop,
    Sleep,
    # Errors
    AgentError,
    AgentLaunchError,
    AgentNotAvailableError,
    AgentReadyTimeoutError,
    SessionNotFoundError,
    SessionAlreadyExistsError,
)
from doeff_agents.adapters.base import AgentType, LaunchConfig
from doeff_agents.monitor import SessionStatus


class TestSessionHandle:
    """Tests for SessionHandle immutable value type."""

    def test_create_handle(self):
        """SessionHandle can be created with required fields."""
        handle = SessionHandle(
            session_name="test-session",
            pane_id="%123",
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
        )
        assert handle.session_name == "test-session"
        assert handle.pane_id == "%123"
        assert handle.agent_type == AgentType.CLAUDE
        assert handle.work_dir == Path("/tmp")
        assert isinstance(handle.started_at, datetime)

    def test_handle_is_frozen(self):
        """SessionHandle is immutable."""
        handle = SessionHandle(
            session_name="test",
            pane_id="%1",
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
        )
        with pytest.raises(AttributeError):
            handle.session_name = "changed"

    def test_handle_equality(self):
        """SessionHandles with same values are equal."""
        ts = datetime.now(timezone.utc)
        h1 = SessionHandle("test", "%1", AgentType.CLAUDE, Path("/tmp"), ts)
        h2 = SessionHandle("test", "%1", AgentType.CLAUDE, Path("/tmp"), ts)
        assert h1 == h2

    def test_handle_repr(self):
        """SessionHandle has readable repr."""
        handle = SessionHandle("test", "%123", AgentType.CLAUDE, Path("/tmp"))
        repr_str = repr(handle)
        assert "test" in repr_str
        assert "%123" in repr_str


class TestObservation:
    """Tests for Observation immutable snapshot."""

    def test_create_observation(self):
        """Observation can be created."""
        obs = Observation(status=SessionStatus.RUNNING)
        assert obs.status == SessionStatus.RUNNING
        assert not obs.output_changed
        assert obs.pr_url is None
        assert obs.output_snippet is None

    def test_observation_is_terminal(self):
        """is_terminal correctly identifies terminal states."""
        assert not Observation(SessionStatus.PENDING).is_terminal
        assert not Observation(SessionStatus.BOOTING).is_terminal
        assert not Observation(SessionStatus.RUNNING).is_terminal
        assert not Observation(SessionStatus.BLOCKED).is_terminal
        assert Observation(SessionStatus.DONE).is_terminal
        assert Observation(SessionStatus.FAILED).is_terminal
        assert Observation(SessionStatus.EXITED).is_terminal
        assert Observation(SessionStatus.STOPPED).is_terminal

    def test_observation_with_all_fields(self):
        """Observation can have all optional fields."""
        obs = Observation(
            status=SessionStatus.BLOCKED,
            output_changed=True,
            pr_url="https://github.com/org/repo/pull/123",
            output_snippet="Some output...",
        )
        assert obs.output_changed
        assert obs.pr_url == "https://github.com/org/repo/pull/123"
        assert obs.output_snippet == "Some output..."


class TestLaunchEffect:
    """Tests for LaunchEffect."""

    def test_create_launch_effect(self):
        """LaunchEffect can be created."""
        config = LaunchConfig(
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
            prompt="Hello",
        )
        effect = Launch("test-session", config)
        assert isinstance(effect, LaunchEffect)
        assert effect.session_name == "test-session"
        assert effect.config == config
        assert effect.ready_timeout == 30.0

    def test_launch_with_custom_timeout(self):
        """LaunchEffect accepts custom timeout."""
        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")
        effect = Launch("test", config, ready_timeout=60.0)
        assert effect.ready_timeout == 60.0


class TestMonitorEffect:
    """Tests for MonitorEffect."""

    def test_create_monitor_effect(self):
        """MonitorEffect can be created."""
        handle = SessionHandle("test", "%1", AgentType.CLAUDE, Path("/tmp"))
        effect = Monitor(handle)
        assert isinstance(effect, MonitorEffect)
        assert effect.handle == handle


class TestCaptureEffect:
    """Tests for CaptureEffect."""

    def test_create_capture_effect(self):
        """CaptureEffect can be created."""
        handle = SessionHandle("test", "%1", AgentType.CLAUDE, Path("/tmp"))
        effect = Capture(handle)
        assert isinstance(effect, CaptureEffect)
        assert effect.handle == handle
        assert effect.lines == 100

    def test_capture_with_custom_lines(self):
        """CaptureEffect accepts custom line count."""
        handle = SessionHandle("test", "%1", AgentType.CLAUDE, Path("/tmp"))
        effect = Capture(handle, lines=500)
        assert effect.lines == 500


class TestSendEffect:
    """Tests for SendEffect."""

    def test_create_send_effect(self):
        """SendEffect can be created."""
        handle = SessionHandle("test", "%1", AgentType.CLAUDE, Path("/tmp"))
        effect = Send(handle, "Hello, world!")
        assert isinstance(effect, SendEffect)
        assert effect.handle == handle
        assert effect.message == "Hello, world!"
        assert effect.enter is True
        assert effect.literal is True

    def test_send_without_enter(self):
        """SendEffect can skip enter."""
        handle = SessionHandle("test", "%1", AgentType.CLAUDE, Path("/tmp"))
        effect = Send(handle, "partial", enter=False)
        assert effect.enter is False


class TestStopEffect:
    """Tests for StopEffect."""

    def test_create_stop_effect(self):
        """StopEffect can be created."""
        handle = SessionHandle("test", "%1", AgentType.CLAUDE, Path("/tmp"))
        effect = Stop(handle)
        assert isinstance(effect, StopEffect)
        assert effect.handle == handle


class TestSleepEffect:
    """Tests for SleepEffect."""

    def test_create_sleep_effect(self):
        """SleepEffect can be created."""
        effect = Sleep(1.5)
        assert isinstance(effect, SleepEffect)
        assert effect.seconds == 1.5


class TestErrorHierarchy:
    """Tests for error classes."""

    def test_error_hierarchy(self):
        """Error classes have correct inheritance."""
        assert issubclass(AgentLaunchError, AgentError)
        assert issubclass(AgentNotAvailableError, AgentLaunchError)
        assert issubclass(AgentReadyTimeoutError, AgentLaunchError)
        assert issubclass(SessionNotFoundError, AgentError)
        assert issubclass(SessionAlreadyExistsError, AgentError)

    def test_errors_are_exceptions(self):
        """All errors are exceptions."""
        assert issubclass(AgentError, Exception)

    def test_error_message(self):
        """Errors can have messages."""
        error = AgentLaunchError("Test error message")
        assert str(error) == "Test error message"
