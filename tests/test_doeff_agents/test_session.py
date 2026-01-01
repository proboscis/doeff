"""Tests for session module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from doeff_agents.adapters.base import AgentType, LaunchConfig
from doeff_agents.monitor import SessionStatus
from doeff_agents.session import (
    AgentLaunchError,
    AgentReadyTimeoutError,
    AgentSession,
    get_adapter,
    register_adapter,
)


class TestAgentSession:
    """Tests for AgentSession dataclass."""

    def test_session_creation(self) -> None:
        """Test basic session creation."""
        session = AgentSession(
            session_name="test",
            pane_id="%42",
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
        )
        assert session.session_name == "test"
        assert session.pane_id == "%42"
        assert session.agent_type == AgentType.CLAUDE
        assert session.status == SessionStatus.PENDING

    def test_is_terminal_for_terminal_states(self) -> None:
        """Test is_terminal returns True for terminal states."""
        for status in [
            SessionStatus.DONE,
            SessionStatus.FAILED,
            SessionStatus.EXITED,
            SessionStatus.STOPPED,
        ]:
            session = AgentSession(
                session_name="test",
                pane_id="%42",
                agent_type=AgentType.CLAUDE,
                work_dir=Path("/tmp"),
                status=status,
            )
            assert session.is_terminal is True

    def test_is_terminal_for_non_terminal_states(self) -> None:
        """Test is_terminal returns False for non-terminal states."""
        for status in [
            SessionStatus.PENDING,
            SessionStatus.BOOTING,
            SessionStatus.RUNNING,
            SessionStatus.BLOCKED,
            SessionStatus.BLOCKED_API,
        ]:
            session = AgentSession(
                session_name="test",
                pane_id="%42",
                agent_type=AgentType.CLAUDE,
                work_dir=Path("/tmp"),
                status=status,
            )
            assert session.is_terminal is False


class TestAdapterRegistry:
    """Tests for adapter registry functions."""

    def test_get_adapter_claude(self) -> None:
        """Test getting Claude adapter."""
        adapter = get_adapter(AgentType.CLAUDE)
        assert adapter.agent_type == AgentType.CLAUDE

    def test_get_adapter_codex(self) -> None:
        """Test getting Codex adapter."""
        adapter = get_adapter(AgentType.CODEX)
        assert adapter.agent_type == AgentType.CODEX

    def test_get_adapter_gemini(self) -> None:
        """Test getting Gemini adapter."""
        adapter = get_adapter(AgentType.GEMINI)
        assert adapter.agent_type == AgentType.GEMINI

    def test_get_adapter_unknown_raises(self) -> None:
        """Test getting unknown adapter raises ValueError."""
        with pytest.raises(ValueError, match="No adapter registered"):
            get_adapter(AgentType.CUSTOM)

    def test_register_custom_adapter(self) -> None:
        """Test registering a custom adapter."""

        class CustomAdapter:
            @property
            def agent_type(self) -> AgentType:
                return AgentType.CUSTOM

            def launch_command(self, cfg: LaunchConfig) -> list[str]:
                return ["custom-agent"]

            def is_available(self) -> bool:
                return True

            @property
            def injection_method(self):
                from doeff_agents.adapters.base import InjectionMethod

                return InjectionMethod.ARG

            @property
            def ready_pattern(self) -> str | None:
                return None

            @property
            def status_bar_lines(self) -> int:
                return 3

        register_adapter(AgentType.CUSTOM, CustomAdapter)  # type: ignore[arg-type]
        adapter = get_adapter(AgentType.CUSTOM)
        assert adapter.agent_type == AgentType.CUSTOM


class TestExceptions:
    """Tests for session exceptions."""

    def test_agent_launch_error(self) -> None:
        """Test AgentLaunchError."""
        error = AgentLaunchError("CLI not available")
        assert str(error) == "CLI not available"

    def test_agent_ready_timeout_error(self) -> None:
        """Test AgentReadyTimeoutError is subclass of AgentLaunchError."""
        assert issubclass(AgentReadyTimeoutError, AgentLaunchError)
        error = AgentReadyTimeoutError("Timeout waiting for agent")
        assert "Timeout" in str(error)


class TestLaunchSessionMocked:
    """Tests for launch_session with mocked tmux."""

    @patch("doeff_agents.session.tmux")
    def test_launch_session_agent_not_available(self, mock_tmux: MagicMock) -> None:
        """Test launch_session raises when agent not available."""
        from doeff_agents.session import launch_session

        config = LaunchConfig(
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
            prompt="Hello",
        )

        with (
            patch("doeff_agents.adapters.claude.shutil.which", return_value=None),
            pytest.raises(AgentLaunchError, match="not available"),
        ):
            launch_session("test", config)

    @patch("doeff_agents.session.tmux")
    @patch("doeff_agents.adapters.claude.shutil.which", return_value="/usr/bin/claude")
    def test_launch_session_success(self, mock_which: MagicMock, mock_tmux: MagicMock) -> None:
        """Test successful session launch."""
        from datetime import datetime, timezone

        from doeff_agents.session import launch_session
        from doeff_agents.tmux import SessionInfo

        mock_tmux.new_session.return_value = SessionInfo(
            session_name="test",
            pane_id="%42",
            created_at=datetime.now(timezone.utc),
        )

        config = LaunchConfig(
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
            prompt="Hello",
        )

        session = launch_session("test", config)
        assert session.session_name == "test"
        assert session.pane_id == "%42"
        assert session.status == SessionStatus.BOOTING


class TestStopSession:
    """Tests for stop_session function."""

    @patch("doeff_agents.session.tmux")
    def test_stop_session_kills_tmux(self, mock_tmux: MagicMock) -> None:
        """Test stop_session kills the tmux session."""
        from doeff_agents.session import stop_session

        mock_tmux.has_session.return_value = True

        session = AgentSession(
            session_name="test",
            pane_id="%42",
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
            status=SessionStatus.RUNNING,
        )

        stop_session(session)

        mock_tmux.kill_session.assert_called_once_with("test")
        assert session.status == SessionStatus.STOPPED

    @patch("doeff_agents.session.tmux")
    def test_stop_session_no_session(self, mock_tmux: MagicMock) -> None:
        """Test stop_session when session doesn't exist."""
        from doeff_agents.session import stop_session

        mock_tmux.has_session.return_value = False

        session = AgentSession(
            session_name="test",
            pane_id="%42",
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
        )

        stop_session(session)

        mock_tmux.kill_session.assert_not_called()
        assert session.status == SessionStatus.STOPPED


class TestSendMessage:
    """Tests for send_message function."""

    @patch("doeff_agents.session.tmux")
    def test_send_message_success(self, mock_tmux: MagicMock) -> None:
        """Test successful message send."""
        from doeff_agents.session import send_message

        mock_tmux.has_session.return_value = True

        session = AgentSession(
            session_name="test",
            pane_id="%42",
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
        )

        send_message(session, "Hello")

        mock_tmux.send_keys.assert_called_once_with("%42", "Hello", enter=True)

    @patch("doeff_agents.session.tmux")
    def test_send_message_session_not_found(self, mock_tmux: MagicMock) -> None:
        """Test send_message raises when session not found."""
        from doeff_agents.session import send_message

        mock_tmux.has_session.return_value = False

        session = AgentSession(
            session_name="test",
            pane_id="%42",
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
        )

        with pytest.raises(RuntimeError, match="does not exist"):
            send_message(session, "Hello")


class TestCaptureOutput:
    """Tests for capture_output function."""

    @patch("doeff_agents.session.tmux")
    def test_capture_output(self, mock_tmux: MagicMock) -> None:
        """Test output capture."""
        from doeff_agents.session import capture_output

        mock_tmux.capture_pane.return_value = "Hello, World!"

        session = AgentSession(
            session_name="test",
            pane_id="%42",
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
        )

        output = capture_output(session)
        assert output == "Hello, World!"
        mock_tmux.capture_pane.assert_called_once_with("%42", 100)
