"""Tests for monitor module."""


from doeff_agents.monitor import (
    MonitorState,
    SessionStatus,
    detect_pr_url,
    detect_status,
    hash_content,
    is_agent_exited,
    is_api_limited,
    is_completed,
    is_failed,
    is_waiting_for_input,
)


class TestSessionStatus:
    """Tests for SessionStatus enum."""

    def test_all_status_values(self) -> None:
        """Test all status values exist."""
        assert SessionStatus.PENDING.value == "pending"
        assert SessionStatus.BOOTING.value == "booting"
        assert SessionStatus.RUNNING.value == "running"
        assert SessionStatus.BLOCKED.value == "blocked"
        assert SessionStatus.BLOCKED_API.value == "blocked_api"
        assert SessionStatus.DONE.value == "done"
        assert SessionStatus.FAILED.value == "failed"
        assert SessionStatus.EXITED.value == "exited"
        assert SessionStatus.STOPPED.value == "stopped"


class TestMonitorState:
    """Tests for MonitorState dataclass."""

    def test_default_values(self) -> None:
        """Test default values."""
        state = MonitorState()
        assert state.output_hash == ""
        assert state.last_output == ""
        assert state.pr_url is None


class TestHashContent:
    """Tests for hash_content function."""

    def test_hash_skips_last_lines(self) -> None:
        """Test that last N lines are skipped."""
        content = "line1\nline2\nline3\nline4\nline5\nline6\nline7"
        hash1 = hash_content(content, skip_lines=3)

        # Changing last 3 lines should not change hash
        content2 = "line1\nline2\nline3\nline4\nXXX\nYYY\nZZZ"
        hash2 = hash_content(content2, skip_lines=3)

        assert hash1 == hash2

    def test_hash_sensitive_to_early_lines(self) -> None:
        """Test that early lines affect hash."""
        content1 = "line1\nline2\nline3\nline4\nline5"
        content2 = "CHANGED\nline2\nline3\nline4\nline5"

        hash1 = hash_content(content1, skip_lines=2)
        hash2 = hash_content(content2, skip_lines=2)

        assert hash1 != hash2

    def test_hash_empty_string(self) -> None:
        """Test hashing empty string."""
        result = hash_content("")
        assert len(result) == 32  # MD5 hex length


class TestIsWaitingForInput:
    """Tests for is_waiting_for_input function."""

    def test_detects_claude_prompt(self) -> None:
        """Test detection of Claude prompt patterns."""
        assert is_waiting_for_input("No, and tell Claude what to do differently")
        assert is_waiting_for_input("↵ send")
        assert is_waiting_for_input("? for shortcuts")

    def test_detects_gemini_prompt(self) -> None:
        """Test detection of Gemini prompt patterns."""
        assert is_waiting_for_input("Type your message")

    def test_no_match_returns_false(self) -> None:
        """Test that no pattern match returns False."""
        assert not is_waiting_for_input("Just some random output")

    def test_custom_patterns(self) -> None:
        """Test custom patterns."""
        assert is_waiting_for_input("CUSTOM_PROMPT>", patterns=["CUSTOM_PROMPT>"])


class TestIsAgentExited:
    """Tests for is_agent_exited function."""

    def test_detects_shell_prompt(self) -> None:
        """Test detection of shell prompts."""
        assert is_agent_exited("some output\n$ ")
        assert is_agent_exited("output\n% ")
        assert is_agent_exited("output\n# ")
        assert is_agent_exited("output\n❯ ")  # noqa: RUF001
        assert is_agent_exited("output\n➜ ")

    def test_detects_git_prompt(self) -> None:
        """Test detection of git prompts."""
        assert is_agent_exited("user@host ~/project git:(main) $ ")

    def test_agent_ui_prevents_exit_detection(self) -> None:
        """Test that agent UI patterns prevent exit detection."""
        # If agent UI is visible, it's not exited
        assert not is_agent_exited("↵ send\n$ ")
        assert not is_agent_exited("? for shortcuts\n$ ")
        assert not is_agent_exited("tokens remaining\n$ ")

    def test_empty_output_returns_false(self) -> None:
        """Test empty output returns False."""
        assert not is_agent_exited("")
        assert not is_agent_exited("\n\n\n")


class TestIsCompleted:
    """Tests for is_completed function."""

    def test_detects_completion_patterns(self) -> None:
        """Test detection of completion patterns."""
        assert is_completed("some work\nTask completed successfully\ndone")
        assert is_completed("work\nAll tasks completed\nend")
        assert is_completed("output\nsession ended\n")
        assert is_completed("output\nGoodbye\n")

    def test_case_insensitive(self) -> None:
        """Test case insensitive matching."""
        assert is_completed("TASK COMPLETED SUCCESSFULLY")
        assert is_completed("task COMPLETED successfully")

    def test_no_match_returns_false(self) -> None:
        """Test no match returns False."""
        assert not is_completed("Just working on stuff")


class TestIsApiLimited:
    """Tests for is_api_limited function."""

    def test_detects_rate_limit_patterns(self) -> None:
        """Test detection of rate limit patterns."""
        assert is_api_limited("Error: cost limit reached")
        assert is_api_limited("rate limit exceeded, please wait")
        assert is_api_limited("quota exceeded for today")
        assert is_api_limited("insufficient quota remaining")
        assert is_api_limited("you've hit your limit")

    def test_no_match_returns_false(self) -> None:
        """Test no match returns False."""
        assert not is_api_limited("Normal operation output")


class TestIsFailed:
    """Tests for is_failed function."""

    def test_detects_failure_patterns(self) -> None:
        """Test detection of failure patterns."""
        assert is_failed("Fatal error occurred")
        assert is_failed("unrecoverable error in system")
        assert is_failed("agent crashed unexpectedly")
        assert is_failed("authentication failed: invalid token")

    def test_no_match_returns_false(self) -> None:
        """Test no match returns False."""
        assert not is_failed("Normal execution")


class TestDetectStatus:
    """Tests for detect_status function."""

    def test_completion_takes_priority_over_exit(self) -> None:
        """Test completion is detected before exit (important for goodbye + shell prompt)."""
        # Agent says goodbye, then shell prompt appears
        output = "Goodbye\n$ "
        state = MonitorState()

        status = detect_status(output, state, output_changed=False, has_prompt=False)
        assert status == SessionStatus.DONE  # Not EXITED!

    def test_api_limit_detected(self) -> None:
        """Test API limit detection."""
        output = "Error: rate limit exceeded"
        state = MonitorState()

        status = detect_status(output, state, output_changed=False, has_prompt=False)
        assert status == SessionStatus.BLOCKED_API

    def test_failure_detected(self) -> None:
        """Test failure detection."""
        output = "Fatal error: system crashed"
        state = MonitorState()

        status = detect_status(output, state, output_changed=False, has_prompt=False)
        assert status == SessionStatus.FAILED

    def test_exit_detected(self) -> None:
        """Test exit detection when no completion."""
        output = "Normal output\n$ "
        state = MonitorState()

        status = detect_status(output, state, output_changed=False, has_prompt=False)
        assert status == SessionStatus.EXITED

    def test_running_when_output_changes(self) -> None:
        """Test running status when output changes."""
        output = "Working..."
        state = MonitorState()

        status = detect_status(output, state, output_changed=True, has_prompt=False)
        assert status == SessionStatus.RUNNING

    def test_blocked_when_prompt_and_stable(self) -> None:
        """Test blocked status when prompt visible and output stable."""
        output = "Waiting for input\n↵ send"
        state = MonitorState()

        status = detect_status(output, state, output_changed=False, has_prompt=True)
        assert status == SessionStatus.BLOCKED

    def test_returns_none_when_no_change(self) -> None:
        """Test None returned when no status change detectable."""
        output = "Just some output without patterns"
        state = MonitorState()

        status = detect_status(output, state, output_changed=False, has_prompt=False)
        assert status is None


class TestDetectPrUrl:
    """Tests for detect_pr_url function."""

    def test_detects_github_pr_url(self) -> None:
        """Test GitHub PR URL detection."""
        output = "Created PR: https://github.com/user/repo/pull/123"
        url = detect_pr_url(output)
        assert url == "https://github.com/user/repo/pull/123"

    def test_detects_gitlab_mr_url(self) -> None:
        """Test GitLab MR URL detection."""
        output = "MR opened: https://gitlab.com/group/project/merge_requests/456"
        url = detect_pr_url(output)
        assert url == "https://gitlab.com/group/project/merge_requests/456"

    def test_returns_none_when_no_url(self) -> None:
        """Test None returned when no PR URL."""
        output = "No PR created"
        url = detect_pr_url(output)
        assert url is None

    def test_returns_first_match(self) -> None:
        """Test first URL is returned when multiple present."""
        output = """
        PR1: https://github.com/user/repo1/pull/1
        PR2: https://github.com/user/repo2/pull/2
        """
        url = detect_pr_url(output)
        assert url == "https://github.com/user/repo1/pull/1"
