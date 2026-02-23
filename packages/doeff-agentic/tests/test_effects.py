"""Tests for doeff_agentic.effects module."""

import pytest
from doeff_agentic.effects import (
    CaptureOutput,
    CaptureOutputEffect,
    RunAgent,
    RunAgentEffect,
    SendMessage,
    SendMessageEffect,
    StopAgent,
    StopAgentEffect,
    WaitForStatus,
    WaitForStatusEffect,
    WaitForUserInput,
    WaitForUserInputEffect,
)
from doeff_agentic.types import AgentConfig, AgentStatus


class TestRunAgentEffect:
    """Tests for RunAgent effect."""

    def test_create_run_agent_effect(self):
        """Test creating a RunAgent effect."""
        config = AgentConfig(
            agent_type="claude",
            prompt="Test prompt",
        )
        effect = RunAgent(config)

        assert isinstance(effect, RunAgentEffect)
        assert effect.config == config
        assert effect.poll_interval == 1.0
        assert effect.ready_timeout == 30.0

    def test_run_agent_with_session_name(self):
        """Test RunAgent with custom session name."""
        config = AgentConfig(agent_type="claude", prompt="Test")
        effect = RunAgent(config, session_name="my-agent")

        assert effect.session_name == "my-agent"

    def test_run_agent_with_custom_poll(self):
        """Test RunAgent with custom poll interval."""
        config = AgentConfig(agent_type="claude", prompt="Test")
        effect = RunAgent(config, poll_interval=2.5)

        assert effect.poll_interval == 2.5

    def test_effect_is_frozen(self):
        """Test that effect is immutable."""
        config = AgentConfig(agent_type="claude", prompt="Test")
        effect = RunAgent(config)

        with pytest.raises(AttributeError):
            setattr(effect, "poll_interval", 5.0)


class TestSendMessageEffect:
    """Tests for SendMessage effect."""

    def test_create_send_message_effect(self):
        """Test creating a SendMessage effect."""
        effect = SendMessage("my-agent", "Hello")

        assert isinstance(effect, SendMessageEffect)
        assert effect.session_name == "my-agent"
        assert effect.message == "Hello"
        assert effect.enter is True

    def test_send_message_without_enter(self):
        """Test SendMessage without pressing Enter."""
        effect = SendMessage("my-agent", "partial", enter=False)

        assert effect.enter is False


class TestWaitForStatusEffect:
    """Tests for WaitForStatus effect."""

    def test_wait_for_single_status(self):
        """Test waiting for a single status."""
        effect = WaitForStatus("my-agent", AgentStatus.BLOCKED)

        assert isinstance(effect, WaitForStatusEffect)
        assert effect.target_status == AgentStatus.BLOCKED
        assert effect.timeout == 300.0

    def test_wait_for_multiple_statuses(self):
        """Test waiting for multiple statuses."""
        targets = (AgentStatus.DONE, AgentStatus.FAILED)
        effect = WaitForStatus("my-agent", targets, timeout=60.0)

        assert effect.target_status == targets
        assert effect.timeout == 60.0

    def test_custom_poll_interval(self):
        """Test custom poll interval."""
        effect = WaitForStatus(
            "my-agent",
            AgentStatus.RUNNING,
            poll_interval=0.5,
        )

        assert effect.poll_interval == 0.5


class TestCaptureOutputEffect:
    """Tests for CaptureOutput effect."""

    def test_create_capture_output_effect(self):
        """Test creating a CaptureOutput effect."""
        effect = CaptureOutput("my-agent")

        assert isinstance(effect, CaptureOutputEffect)
        assert effect.session_name == "my-agent"
        assert effect.lines == 100

    def test_capture_with_custom_lines(self):
        """Test CaptureOutput with custom line count."""
        effect = CaptureOutput("my-agent", lines=500)

        assert effect.lines == 500


class TestWaitForUserInputEffect:
    """Tests for WaitForUserInput effect."""

    def test_create_wait_for_user_input(self):
        """Test creating a WaitForUserInput effect."""
        effect = WaitForUserInput("my-agent", "Approve? [y/n]")

        assert isinstance(effect, WaitForUserInputEffect)
        assert effect.session_name == "my-agent"
        assert effect.prompt == "Approve? [y/n]"
        assert effect.timeout is None

    def test_wait_with_timeout(self):
        """Test WaitForUserInput with timeout."""
        effect = WaitForUserInput("my-agent", "Approve?", timeout=60.0)

        assert effect.timeout == 60.0


class TestStopAgentEffect:
    """Tests for StopAgent effect."""

    def test_create_stop_agent_effect(self):
        """Test creating a StopAgent effect."""
        effect = StopAgent("my-agent")

        assert isinstance(effect, StopAgentEffect)
        assert effect.session_name == "my-agent"


class TestEffectImmutable:
    """Tests for effect immutability."""

    def test_effect_is_frozen(self):
        """Test that effects are immutable dataclasses."""
        config = AgentConfig(agent_type="claude", prompt="Test")
        effect = RunAgent(config)

        # Effects should be frozen dataclasses
        import pytest
        with pytest.raises(AttributeError):
            effect.poll_interval = 5.0  # type: ignore
