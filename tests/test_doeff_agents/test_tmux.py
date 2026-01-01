"""Tests for tmux module."""

import pytest
from doeff_agents.tmux import (
    ANSI_PATTERN,
    SessionAlreadyExistsError,
    SessionConfig,
    SessionInfo,
    SessionNotFoundError,
    TmuxError,
    TmuxNotAvailableError,
    strip_ansi,
)


class TestStripAnsi:
    """Tests for ANSI escape sequence stripping."""

    def test_strip_ansi_removes_color_codes(self) -> None:
        """Test that color codes are removed."""
        text = "\x1b[31mRed text\x1b[0m"
        result = strip_ansi(text)
        assert result == "Red text"

    def test_strip_ansi_removes_cursor_codes(self) -> None:
        """Test that cursor codes are removed."""
        text = "\x1b[2J\x1b[HHello"
        result = strip_ansi(text)
        assert result == "Hello"

    def test_strip_ansi_preserves_plain_text(self) -> None:
        """Test that plain text is preserved."""
        text = "Hello, World!"
        result = strip_ansi(text)
        assert result == text

    def test_strip_ansi_handles_empty_string(self) -> None:
        """Test empty string handling."""
        assert strip_ansi("") == ""

    def test_strip_ansi_multiple_sequences(self) -> None:
        """Test multiple ANSI sequences."""
        text = "\x1b[1m\x1b[32mBold Green\x1b[0m Normal"
        result = strip_ansi(text)
        assert result == "Bold Green Normal"


class TestSessionConfig:
    """Tests for SessionConfig dataclass."""

    def test_session_config_creation(self) -> None:
        """Test basic SessionConfig creation."""
        config = SessionConfig(session_name="test-session")
        assert config.session_name == "test-session"
        assert config.work_dir is None
        assert config.env is None
        assert config.window_name is None

    def test_session_config_with_all_options(self) -> None:
        """Test SessionConfig with all options."""
        from pathlib import Path

        config = SessionConfig(
            session_name="test",
            work_dir=Path("/tmp"),
            env={"FOO": "bar"},
            window_name="main",
        )
        assert config.session_name == "test"
        assert config.work_dir == Path("/tmp")
        assert config.env == {"FOO": "bar"}
        assert config.window_name == "main"

    def test_session_config_is_frozen(self) -> None:
        """Test that SessionConfig is immutable."""
        config = SessionConfig(session_name="test")
        with pytest.raises(AttributeError):
            config.session_name = "changed"  # type: ignore[misc]


class TestSessionInfo:
    """Tests for SessionInfo dataclass."""

    def test_session_info_creation(self) -> None:
        """Test SessionInfo creation."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        info = SessionInfo(session_name="test", pane_id="%42", created_at=now)
        assert info.session_name == "test"
        assert info.pane_id == "%42"
        assert info.created_at == now


class TestExceptions:
    """Tests for exception hierarchy."""

    def test_tmux_error_is_base(self) -> None:
        """Test TmuxError is the base exception."""
        assert issubclass(TmuxNotAvailableError, TmuxError)
        assert issubclass(SessionNotFoundError, TmuxError)
        assert issubclass(SessionAlreadyExistsError, TmuxError)

    def test_exception_messages(self) -> None:
        """Test exception messages."""
        e = TmuxNotAvailableError("tmux not found")
        assert str(e) == "tmux not found"


class TestAnsiPattern:
    """Tests for the ANSI pattern regex."""

    def test_pattern_matches_basic_color(self) -> None:
        """Test pattern matches basic color codes."""
        assert ANSI_PATTERN.search("\x1b[31m") is not None

    def test_pattern_matches_reset(self) -> None:
        """Test pattern matches reset code."""
        assert ANSI_PATTERN.search("\x1b[0m") is not None

    def test_pattern_matches_multi_param(self) -> None:
        """Test pattern matches multi-parameter codes."""
        assert ANSI_PATTERN.search("\x1b[1;32m") is not None
