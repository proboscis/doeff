"""Tests for agent Programs."""

import pytest
from pathlib import Path

from doeff_agents.effects import (
    LaunchEffect,
    MonitorEffect,
    CaptureEffect,
    SendEffect,
    StopEffect,
    SleepEffect,
    SessionHandle,
)
from doeff_agents.programs import (
    AgentResult,
    run_agent_to_completion,
    with_session,
    monitor_until_terminal,
    monitor_once,
    wait_and_monitor,
)
from doeff_agents.handlers import (
    MockAgentHandler,
    MockSessionScript,
    dispatch_effect,
)
from doeff_agents.adapters.base import AgentType, LaunchConfig
from doeff_agents.monitor import SessionStatus


def run_program_with_mock(program, handler):
    """Execute a program generator with a mock handler.

    This simulates what a doeff interpreter would do.
    """
    try:
        effect = next(program)
    except StopIteration as stop:
        return stop.value

    while True:
        try:
            result = dispatch_effect(handler, effect)
            effect = program.send(result)
        except StopIteration as stop:
            return stop.value


class TestAgentResult:
    """Tests for AgentResult."""

    def test_succeeded_when_done(self):
        """succeeded is True when status is DONE."""
        result = AgentResult(
            handle=SessionHandle("test", "%1", AgentType.CLAUDE, Path("/tmp")),
            final_status=SessionStatus.DONE,
            output="Success",
        )
        assert result.succeeded
        assert not result.failed

    def test_failed_when_failed(self):
        """failed is True when status is FAILED."""
        result = AgentResult(
            handle=SessionHandle("test", "%1", AgentType.CLAUDE, Path("/tmp")),
            final_status=SessionStatus.FAILED,
            output="Error",
        )
        assert result.failed
        assert not result.succeeded


class TestMonitorOnce:
    """Tests for monitor_once program."""

    def test_monitor_once_yields_monitor_effect(self):
        """monitor_once yields a MonitorEffect."""
        handle = SessionHandle("test", "%1", AgentType.CLAUDE, Path("/tmp"))
        program = monitor_once(handle)

        effect = next(program)

        assert isinstance(effect, MonitorEffect)
        assert effect.handle == handle


class TestWaitAndMonitor:
    """Tests for wait_and_monitor program."""

    def test_wait_and_monitor_sleeps_then_monitors(self):
        """wait_and_monitor yields Sleep then Monitor."""
        handle = SessionHandle("test", "%1", AgentType.CLAUDE, Path("/tmp"))
        program = wait_and_monitor(handle, poll_interval=2.0)

        effect1 = next(program)
        assert isinstance(effect1, SleepEffect)
        assert effect1.seconds == 2.0

        effect2 = program.send(None)  # Sleep returns None
        assert isinstance(effect2, MonitorEffect)


class TestMonitorUntilTerminal:
    """Tests for monitor_until_terminal program."""

    def test_monitor_until_done(self):
        """Monitors until DONE status."""
        handler = MockAgentHandler()
        handler.configure_session(
            "test",
            MockSessionScript([
                (SessionStatus.RUNNING, "Working..."),
                (SessionStatus.RUNNING, "Still working..."),
                (SessionStatus.DONE, "Complete!"),
            ]),
        )

        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")
        handle = handler.handle_launch(
            LaunchEffect(session_name="test", config=config)
        )

        program = monitor_until_terminal(handle, poll_interval=0.1)
        observation, iterations = run_program_with_mock(program, handler)

        assert observation.status == SessionStatus.DONE
        assert observation.is_terminal
        assert iterations == 2  # Two sleeps before terminal

    def test_monitor_respects_max_iterations(self):
        """Monitoring respects max_iterations limit."""
        handler = MockAgentHandler()
        # Script that never terminates
        handler.configure_session(
            "test",
            MockSessionScript([
                (SessionStatus.RUNNING, "Working..."),
                (SessionStatus.RUNNING, "Still working..."),
                (SessionStatus.RUNNING, "More work..."),
                (SessionStatus.RUNNING, "Even more..."),
            ]),
        )

        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")
        handle = handler.handle_launch(
            LaunchEffect(session_name="test", config=config)
        )

        program = monitor_until_terminal(
            handle,
            poll_interval=0.1,
            max_iterations=2,
        )
        observation, iterations = run_program_with_mock(program, handler)

        assert observation.status == SessionStatus.RUNNING
        assert iterations == 2


class TestRunAgentToCompletion:
    """Tests for run_agent_to_completion program."""

    def test_full_lifecycle(self):
        """run_agent_to_completion handles full session lifecycle."""
        handler = MockAgentHandler()
        handler.configure_session(
            "test",
            MockSessionScript([
                (SessionStatus.BOOTING, "Starting..."),
                (SessionStatus.RUNNING, "Working..."),
                (SessionStatus.DONE, "Task completed successfully!"),
            ]),
        )

        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")

        program = run_agent_to_completion(
            "test",
            config,
            poll_interval=0.1,
        )
        result = run_program_with_mock(program, handler)

        assert isinstance(result, AgentResult)
        assert result.succeeded
        assert result.final_status == SessionStatus.DONE
        # Output is captured from the final monitor state
        assert "completed" in result.output
        assert result.handle.session_name == "test"

    def test_stops_session_on_completion(self):
        """Session is stopped after completion."""
        handler = MockAgentHandler()
        handler.configure_session(
            "test",
            MockSessionScript([
                (SessionStatus.DONE, "Done!"),
            ]),
        )

        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")

        program = run_agent_to_completion("test", config, poll_interval=0.1)
        run_program_with_mock(program, handler)

        # Verify session was stopped (next monitor should show STOPPED or equivalent)
        # The handler marks it as stopped internally
        handle = handler._handles.get("test")
        assert handle is not None
        assert handler._statuses.get("test") == SessionStatus.STOPPED

    def test_respects_timeout(self):
        """run_agent_to_completion respects timeout_iterations."""
        handler = MockAgentHandler()
        handler.configure_session(
            "test",
            MockSessionScript([
                (SessionStatus.RUNNING, "Working..."),
                (SessionStatus.RUNNING, "Still working..."),
                (SessionStatus.RUNNING, "More work..."),
            ]),
        )

        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")

        program = run_agent_to_completion(
            "test",
            config,
            poll_interval=0.1,
            timeout_iterations=2,
        )
        result = run_program_with_mock(program, handler)

        assert result.final_status == SessionStatus.RUNNING
        assert result.iterations == 2


class TestWithSession:
    """Tests for with_session bracket program."""

    def test_with_session_basic(self):
        """with_session launches, runs use, then stops."""
        handler = MockAgentHandler()

        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")

        def use_session(handle):
            # Just capture output
            output = yield CaptureEffect(handle=handle, lines=50)
            return output

        handler.configure_session("test", initial_output="Test output")

        program = with_session("test", config, use_session)
        result = run_program_with_mock(program, handler)

        assert result == "Test output"
        assert handler._statuses.get("test") == SessionStatus.STOPPED

    def test_with_session_stops_on_exception(self):
        """with_session stops even when use raises."""
        handler = MockAgentHandler()
        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")

        def failing_use(handle):
            yield CaptureEffect(handle=handle, lines=50)
            raise ValueError("Simulated error")

        program = with_session("test", config, failing_use)

        with pytest.raises(ValueError, match="Simulated error"):
            run_program_with_mock(program, handler)

        # Session should still be stopped
        assert handler._statuses.get("test") == SessionStatus.STOPPED


class TestProgramEffectSequence:
    """Tests verifying the exact sequence of effects yielded by programs."""

    def test_run_agent_effect_sequence(self):
        """run_agent_to_completion yields effects in correct order."""
        handler = MockAgentHandler()
        handler.configure_session(
            "test",
            MockSessionScript([
                (SessionStatus.DONE, "Done!"),
            ]),
        )

        config = LaunchConfig(AgentType.CLAUDE, Path("/tmp"), "Hello")
        program = run_agent_to_completion("test", config, poll_interval=0.1)

        effects = []
        try:
            effect = next(program)
            effects.append(type(effect).__name__)
            while True:
                result = dispatch_effect(handler, effect)
                effect = program.send(result)
                effects.append(type(effect).__name__)
        except StopIteration:
            pass

        # Expected: Launch -> Monitor -> (loops until terminal) -> Capture -> Stop
        assert effects[0] == "LaunchEffect"
        assert effects[1] == "MonitorEffect"
        # Last two should be Capture and Stop
        assert effects[-2] == "CaptureEffect"
        assert effects[-1] == "StopEffect"
