"""Tests for agent effect handlers."""

import pytest
from pathlib import Path

from doeff_agents.effects import (
    SessionHandle,
    Observation,
    Launch,
    Monitor,
    Capture,
    Send,
    Stop,
    Sleep,
    SessionAlreadyExistsError,
    SessionNotFoundError,
)
from doeff_agents.handlers import (
    MockAgentHandler,
    MockSessionScript,
    dispatch_effect,
)
from doeff_agents.adapters.base import AgentType, LaunchConfig
from doeff_agents.monitor import SessionStatus


class TestMockAgentHandler:
    """Tests for MockAgentHandler."""

    def test_launch_creates_handle(self):
        """Launch effect creates a SessionHandle."""
        handler = MockAgentHandler()
        config = LaunchConfig(
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
            prompt="Hello",
        )
        effect = Launch("test-session", config)

        handle = handler.handle_launch(effect)

        assert isinstance(handle, SessionHandle)
        assert handle.session_name == "test-session"
        assert handle.agent_type == AgentType.CLAUDE

    def test_launch_duplicate_raises(self):
        """Launching duplicate session raises error."""
        handler = MockAgentHandler()
        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")
        effect = Launch("test", config)

        handler.handle_launch(effect)

        with pytest.raises(SessionAlreadyExistsError):
            handler.handle_launch(effect)

    def test_monitor_without_script_returns_booting(self):
        """Monitor without script returns BOOTING status (initial state)."""
        handler = MockAgentHandler()
        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")
        handle = handler.handle_launch(Launch("test", config))

        obs = handler.handle_monitor(Monitor(handle))

        assert isinstance(obs, Observation)
        assert obs.status == SessionStatus.BOOTING  # Initial state after launch
        assert not obs.output_changed

    def test_monitor_with_script(self):
        """Monitor follows script observations."""
        handler = MockAgentHandler()

        # Pre-configure with script
        script = MockSessionScript(
            observations=[
                (SessionStatus.RUNNING, "Processing..."),
                (SessionStatus.BLOCKED, "Waiting for input..."),
                (SessionStatus.DONE, "Complete!"),
            ]
        )
        handler.configure_session("test", script)

        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")
        handle = handler.handle_launch(Launch("test", config))

        # First observation
        obs1 = handler.handle_monitor(Monitor(handle))
        assert obs1.status == SessionStatus.RUNNING
        assert obs1.output_changed

        # Second observation
        obs2 = handler.handle_monitor(Monitor(handle))
        assert obs2.status == SessionStatus.BLOCKED

        # Third observation
        obs3 = handler.handle_monitor(Monitor(handle))
        assert obs3.status == SessionStatus.DONE
        assert obs3.is_terminal

    def test_monitor_nonexistent_returns_exited(self):
        """Monitoring non-existent session returns EXITED."""
        handler = MockAgentHandler()
        fake_handle = SessionHandle("missing", "%0", AgentType.CLAUDE, Path("/tmp"))

        obs = handler.handle_monitor(Monitor(fake_handle))

        assert obs.status == SessionStatus.EXITED

    def test_capture_returns_configured_output(self):
        """Capture returns configured output."""
        handler = MockAgentHandler()
        handler.configure_session("test", initial_output="Hello, world!")

        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")
        handle = handler.handle_launch(Launch("test", config))

        output = handler.handle_capture(Capture(handle))

        assert output == "Hello, world!"

    def test_capture_nonexistent_raises(self):
        """Capturing from non-existent session raises error."""
        handler = MockAgentHandler()
        fake_handle = SessionHandle("missing", "%0", AgentType.CLAUDE, Path("/tmp"))

        with pytest.raises(SessionNotFoundError):
            handler.handle_capture(Capture(fake_handle))

    def test_send_records_message(self):
        """Send effect records message for verification."""
        handler = MockAgentHandler()
        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")
        handle = handler.handle_launch(Launch("test", config))

        handler.handle_send(Send(handle, "First message"))
        handler.handle_send(Send(handle, "Second message"))

        assert handler.sent_messages == [
            ("test", "First message"),
            ("test", "Second message"),
        ]

    def test_send_nonexistent_raises(self):
        """Sending to non-existent session raises error."""
        handler = MockAgentHandler()
        fake_handle = SessionHandle("missing", "%0", AgentType.CLAUDE, Path("/tmp"))

        with pytest.raises(SessionNotFoundError):
            handler.handle_send(Send(fake_handle, "message"))

    def test_stop_marks_session_stopped(self):
        """Stop effect marks session as stopped."""
        handler = MockAgentHandler()
        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")
        handle = handler.handle_launch(Launch("test", config))

        handler.handle_stop(Stop(handle))

        # Monitoring after stop should show STOPPED
        obs = handler.handle_monitor(Monitor(handle))
        assert obs.status == SessionStatus.STOPPED

    def test_sleep_records_time(self):
        """Sleep effect records time without actual delay."""
        handler = MockAgentHandler()

        handler.handle_sleep(Sleep(1.5))
        handler.handle_sleep(Sleep(2.5))

        assert handler.total_sleep_time == 4.0

    def test_script_exhaustion_returns_done(self):
        """When script is exhausted, observations return DONE."""
        handler = MockAgentHandler()
        script = MockSessionScript(
            observations=[
                (SessionStatus.RUNNING, "Working..."),
            ]
        )
        handler.configure_session("test", script)

        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")
        handle = handler.handle_launch(Launch("test", config))

        # First observation from script
        obs1 = handler.handle_monitor(Monitor(handle))
        assert obs1.status == SessionStatus.RUNNING

        # Second observation - script exhausted, returns DONE
        obs2 = handler.handle_monitor(Monitor(handle))
        assert obs2.status == SessionStatus.DONE


class TestDispatchEffect:
    """Tests for effect dispatching."""

    def test_dispatch_launch(self):
        """dispatch_effect handles LaunchEffect."""
        handler = MockAgentHandler()
        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")
        effect = Launch("test", config)

        result = dispatch_effect(handler, effect)

        assert isinstance(result, SessionHandle)

    def test_dispatch_monitor(self):
        """dispatch_effect handles MonitorEffect."""
        handler = MockAgentHandler()
        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")
        handle = handler.handle_launch(Launch("test", config))

        result = dispatch_effect(handler, Monitor(handle))

        assert isinstance(result, Observation)

    def test_dispatch_sleep(self):
        """dispatch_effect handles SleepEffect."""
        handler = MockAgentHandler()

        result = dispatch_effect(handler, Sleep(1.0))

        assert result is None
        assert handler.total_sleep_time == 1.0

    def test_dispatch_unknown_raises(self):
        """dispatch_effect raises for unknown effect types."""
        handler = MockAgentHandler()

        with pytest.raises(TypeError, match="Unknown effect type"):
            dispatch_effect(handler, "not an effect")


class TestMockSessionScript:
    """Tests for MockSessionScript."""

    def test_empty_script_returns_done(self):
        """Empty script returns DONE immediately."""
        script = MockSessionScript()

        status, output = script.next_observation()

        assert status == SessionStatus.DONE
        assert output == ""

    def test_script_consumes_in_order(self):
        """Script observations are consumed in order."""
        script = MockSessionScript(
            observations=[
                (SessionStatus.BOOTING, "Starting..."),
                (SessionStatus.RUNNING, "Working..."),
                (SessionStatus.DONE, "Done!"),
            ]
        )

        s1, o1 = script.next_observation()
        assert s1 == SessionStatus.BOOTING
        assert o1 == "Starting..."

        s2, o2 = script.next_observation()
        assert s2 == SessionStatus.RUNNING
        assert o2 == "Working..."

        s3, o3 = script.next_observation()
        assert s3 == SessionStatus.DONE
        assert o3 == "Done!"

        # After exhaustion
        s4, o4 = script.next_observation()
        assert s4 == SessionStatus.DONE
        assert o4 == ""


class TestMultipleSessions:
    """Tests for handling multiple sessions."""

    def test_multiple_sessions_independent(self):
        """Multiple sessions operate independently."""
        handler = MockAgentHandler()
        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")

        # Configure different scripts
        handler.configure_session(
            "session1",
            MockSessionScript([(SessionStatus.DONE, "Done 1")]),
        )
        handler.configure_session(
            "session2",
            MockSessionScript([
                (SessionStatus.RUNNING, "Working..."),
                (SessionStatus.BLOCKED, "Blocked..."),
            ]),
        )

        handle1 = handler.handle_launch(Launch("session1", config))
        handle2 = handler.handle_launch(Launch("session2", config))

        obs1 = handler.handle_monitor(Monitor(handle1))
        obs2 = handler.handle_monitor(Monitor(handle2))

        assert obs1.status == SessionStatus.DONE
        assert obs2.status == SessionStatus.RUNNING

        obs2b = handler.handle_monitor(Monitor(handle2))
        assert obs2b.status == SessionStatus.BLOCKED
