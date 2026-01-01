"""Tests for CESK-compatible effect handlers."""

from pathlib import Path

import pytest
from doeff_agents.adapters.base import AgentType, LaunchConfig
from doeff_agents.cesk_handlers import (
    AGENT_SESSIONS_KEY,
    MOCK_AGENT_STATE_KEY,
    MockAgentState,
    MockSessionScript,
    _get_mock_state,
    _get_sessions,
    agent_effectful_handlers,
    configure_mock_session,
    mock_agent_handlers,
)
from doeff_agents.effects import (
    CaptureEffect,
    LaunchEffect,
    MonitorEffect,
    SendEffect,
    SessionAlreadyExistsError,
    SessionHandle,
    SessionNotFoundError,
    SleepEffect,
    StopEffect,
)
from doeff_agents.monitor import SessionStatus

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def empty_env():
    """Empty environment for CESK handlers."""
    return {}


@pytest.fixture
def empty_store():
    """Empty store for CESK handlers."""
    return {}


@pytest.fixture
def mock_handlers():
    """Get mock handler registry."""
    return mock_agent_handlers()


@pytest.fixture
def sample_config():
    """Sample launch configuration."""
    return LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path("/tmp"),
        prompt="Hello, world!",
    )


# =============================================================================
# Test Mock Launch Handler
# =============================================================================


class TestMockLaunchHandler:
    """Tests for mock launch handler."""

    @pytest.mark.asyncio
    async def test_launch_creates_handle(self, mock_handlers, empty_env, empty_store, sample_config):
        """Launch effect creates a SessionHandle."""
        handler = mock_handlers[LaunchEffect]
        effect = LaunchEffect(session_name="test", config=sample_config)

        handle, _store = await handler(effect, empty_env, empty_store)

        assert isinstance(handle, SessionHandle)
        assert handle.session_name == "test"
        assert handle.agent_type == AgentType.CLAUDE

    @pytest.mark.asyncio
    async def test_launch_stores_state(self, mock_handlers, empty_env, empty_store, sample_config):
        """Launch stores session state in mock state."""
        handler = mock_handlers[LaunchEffect]
        effect = LaunchEffect(session_name="test", config=sample_config)

        _handle, store = await handler(effect, empty_env, empty_store)

        mock_state = _get_mock_state(store)
        assert "test" in mock_state.handles
        assert mock_state.statuses["test"] == SessionStatus.BOOTING

    @pytest.mark.asyncio
    async def test_launch_duplicate_raises(self, mock_handlers, empty_env, empty_store, sample_config):
        """Launching duplicate session raises error."""
        handler = mock_handlers[LaunchEffect]
        effect = LaunchEffect(session_name="test", config=sample_config)

        _, store = await handler(effect, empty_env, empty_store)

        with pytest.raises(SessionAlreadyExistsError):
            await handler(effect, empty_env, store)


# =============================================================================
# Test Mock Monitor Handler
# =============================================================================


class TestMockMonitorHandler:
    """Tests for mock monitor handler."""

    @pytest.mark.asyncio
    async def test_monitor_without_script(self, mock_handlers, empty_env, empty_store, sample_config):
        """Monitor without script returns initial status."""
        launch_handler = mock_handlers[LaunchEffect]
        monitor_handler = mock_handlers[MonitorEffect]

        launch_effect = LaunchEffect(session_name="test", config=sample_config)
        handle, store = await launch_handler(launch_effect, empty_env, empty_store)

        monitor_effect = MonitorEffect(handle=handle)
        observation, store = await monitor_handler(monitor_effect, empty_env, store)

        assert observation.status == SessionStatus.BOOTING
        assert not observation.output_changed

    @pytest.mark.asyncio
    async def test_monitor_with_script(self, mock_handlers, empty_env, empty_store, sample_config):
        """Monitor follows script observations."""
        # Pre-configure script
        script = MockSessionScript(
            observations=[
                (SessionStatus.RUNNING, "Processing..."),
                (SessionStatus.BLOCKED, "Waiting for input..."),
                (SessionStatus.DONE, "Complete!"),
            ]
        )
        configure_mock_session(empty_store, "test", script)

        launch_handler = mock_handlers[LaunchEffect]
        monitor_handler = mock_handlers[MonitorEffect]

        launch_effect = LaunchEffect(session_name="test", config=sample_config)
        handle, store = await launch_handler(launch_effect, empty_env, empty_store)

        # First observation
        obs1, store = await monitor_handler(MonitorEffect(handle=handle), empty_env, store)
        assert obs1.status == SessionStatus.RUNNING
        assert obs1.output_changed

        # Second observation
        obs2, store = await monitor_handler(MonitorEffect(handle=handle), empty_env, store)
        assert obs2.status == SessionStatus.BLOCKED

        # Third observation
        obs3, store = await monitor_handler(MonitorEffect(handle=handle), empty_env, store)
        assert obs3.status == SessionStatus.DONE
        assert obs3.is_terminal

    @pytest.mark.asyncio
    async def test_monitor_nonexistent_returns_exited(self, mock_handlers, empty_env, empty_store):
        """Monitoring non-existent session returns EXITED."""
        monitor_handler = mock_handlers[MonitorEffect]
        fake_handle = SessionHandle("missing", "%0", AgentType.CLAUDE, Path("/tmp"))

        obs, _store = await monitor_handler(MonitorEffect(handle=fake_handle), empty_env, empty_store)

        assert obs.status == SessionStatus.EXITED


# =============================================================================
# Test Mock Capture Handler
# =============================================================================


class TestMockCaptureHandler:
    """Tests for mock capture handler."""

    @pytest.mark.asyncio
    async def test_capture_returns_output(self, mock_handlers, empty_env, empty_store, sample_config):
        """Capture returns configured output."""
        configure_mock_session(empty_store, "test", initial_output="Hello, world!")

        launch_handler = mock_handlers[LaunchEffect]
        capture_handler = mock_handlers[CaptureEffect]

        launch_effect = LaunchEffect(session_name="test", config=sample_config)
        handle, store = await launch_handler(launch_effect, empty_env, empty_store)

        capture_effect = CaptureEffect(handle=handle, lines=50)
        output, store = await capture_handler(capture_effect, empty_env, store)

        assert output == "Hello, world!"

    @pytest.mark.asyncio
    async def test_capture_nonexistent_raises(self, mock_handlers, empty_env, empty_store):
        """Capturing from non-existent session raises error."""
        capture_handler = mock_handlers[CaptureEffect]
        fake_handle = SessionHandle("missing", "%0", AgentType.CLAUDE, Path("/tmp"))

        with pytest.raises(SessionNotFoundError):
            await capture_handler(CaptureEffect(handle=fake_handle), empty_env, empty_store)


# =============================================================================
# Test Mock Send Handler
# =============================================================================


class TestMockSendHandler:
    """Tests for mock send handler."""

    @pytest.mark.asyncio
    async def test_send_records_message(self, mock_handlers, empty_env, empty_store, sample_config):
        """Send effect records message."""
        launch_handler = mock_handlers[LaunchEffect]
        send_handler = mock_handlers[SendEffect]

        launch_effect = LaunchEffect(session_name="test", config=sample_config)
        handle, store = await launch_handler(launch_effect, empty_env, empty_store)

        send_effect = SendEffect(handle=handle, message="First message")
        _, store = await send_handler(send_effect, empty_env, store)

        send_effect2 = SendEffect(handle=handle, message="Second message")
        _, store = await send_handler(send_effect2, empty_env, store)

        mock_state = _get_mock_state(store)
        assert mock_state.sends == [("test", "First message"), ("test", "Second message")]

    @pytest.mark.asyncio
    async def test_send_nonexistent_raises(self, mock_handlers, empty_env, empty_store):
        """Sending to non-existent session raises error."""
        send_handler = mock_handlers[SendEffect]
        fake_handle = SessionHandle("missing", "%0", AgentType.CLAUDE, Path("/tmp"))

        with pytest.raises(SessionNotFoundError):
            await send_handler(SendEffect(handle=fake_handle, message="test"), empty_env, empty_store)


# =============================================================================
# Test Mock Stop Handler
# =============================================================================


class TestMockStopHandler:
    """Tests for mock stop handler."""

    @pytest.mark.asyncio
    async def test_stop_marks_session_stopped(self, mock_handlers, empty_env, empty_store, sample_config):
        """Stop effect marks session as stopped."""
        launch_handler = mock_handlers[LaunchEffect]
        stop_handler = mock_handlers[StopEffect]

        launch_effect = LaunchEffect(session_name="test", config=sample_config)
        handle, store = await launch_handler(launch_effect, empty_env, empty_store)

        stop_effect = StopEffect(handle=handle)
        _, store = await stop_handler(stop_effect, empty_env, store)

        mock_state = _get_mock_state(store)
        assert mock_state.statuses["test"] == SessionStatus.STOPPED


# =============================================================================
# Test Mock Sleep Handler
# =============================================================================


class TestMockSleepHandler:
    """Tests for mock sleep handler."""

    @pytest.mark.asyncio
    async def test_sleep_records_time(self, mock_handlers, empty_env, empty_store):
        """Sleep effect records time without actual delay."""
        sleep_handler = mock_handlers[SleepEffect]

        _, store = await sleep_handler(SleepEffect(seconds=1.5), empty_env, empty_store)
        _, store = await sleep_handler(SleepEffect(seconds=2.5), empty_env, store)

        mock_state = _get_mock_state(store)
        assert mock_state.sleep_calls == [1.5, 2.5]


# =============================================================================
# Test Handler Registry
# =============================================================================


class TestHandlerRegistry:
    """Tests for handler registries."""

    def test_agent_effectful_handlers_keys(self):
        """agent_effectful_handlers returns handlers for all effects."""
        handlers = agent_effectful_handlers()

        assert LaunchEffect in handlers
        assert MonitorEffect in handlers
        assert CaptureEffect in handlers
        assert SendEffect in handlers
        assert StopEffect in handlers
        assert SleepEffect in handlers

    def test_mock_agent_handlers_keys(self):
        """mock_agent_handlers returns handlers for all effects."""
        handlers = mock_agent_handlers()

        assert LaunchEffect in handlers
        assert MonitorEffect in handlers
        assert CaptureEffect in handlers
        assert SendEffect in handlers
        assert StopEffect in handlers
        assert SleepEffect in handlers


# =============================================================================
# Test Store State Management
# =============================================================================


class TestStoreStateManagement:
    """Tests for store state management functions."""

    def test_get_sessions_creates_if_missing(self):
        """_get_sessions creates sessions dict if missing."""
        store = {}

        sessions = _get_sessions(store)

        assert sessions == {}
        assert AGENT_SESSIONS_KEY in store

    def test_get_sessions_returns_existing(self):
        """_get_sessions returns existing sessions dict."""
        store = {AGENT_SESSIONS_KEY: {"test": "session"}}

        sessions = _get_sessions(store)

        assert sessions == {"test": "session"}

    def test_get_mock_state_creates_if_missing(self):
        """_get_mock_state creates state if missing."""
        store = {}

        state = _get_mock_state(store)

        assert isinstance(state, MockAgentState)
        assert MOCK_AGENT_STATE_KEY in store

    def test_get_mock_state_returns_existing(self):
        """_get_mock_state returns existing state."""
        existing_state = MockAgentState()
        existing_state.next_pane_id = 42
        store = {MOCK_AGENT_STATE_KEY: existing_state}

        state = _get_mock_state(store)

        assert state.next_pane_id == 42


# =============================================================================
# Test MockSessionScript
# =============================================================================


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


# =============================================================================
# Test Full CESK Workflow
# =============================================================================


class TestCeskWorkflow:
    """Tests for complete CESK workflow scenarios."""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, mock_handlers, empty_env, empty_store, sample_config):
        """Test complete session lifecycle through CESK handlers."""
        # Configure script
        script = MockSessionScript(
            observations=[
                (SessionStatus.RUNNING, "Working..."),
                (SessionStatus.DONE, "Complete!"),
            ]
        )
        configure_mock_session(empty_store, "test", script, initial_output="Final output")

        store = empty_store

        # Launch
        launch_handler = mock_handlers[LaunchEffect]
        handle, store = await launch_handler(
            LaunchEffect(session_name="test", config=sample_config),
            empty_env,
            store,
        )
        assert handle.session_name == "test"

        # Monitor until terminal
        monitor_handler = mock_handlers[MonitorEffect]
        sleep_handler = mock_handlers[SleepEffect]

        while True:
            obs, store = await monitor_handler(MonitorEffect(handle=handle), empty_env, store)
            if obs.is_terminal:
                break
            _, store = await sleep_handler(SleepEffect(seconds=1.0), empty_env, store)

        assert obs.status == SessionStatus.DONE

        # Capture output
        capture_handler = mock_handlers[CaptureEffect]
        output, store = await capture_handler(
            CaptureEffect(handle=handle, lines=50),
            empty_env,
            store,
        )
        # Note: script overrides initial_output, so we get the last script output
        assert "Complete!" in output or output == "Final output"

        # Stop
        stop_handler = mock_handlers[StopEffect]
        _, store = await stop_handler(StopEffect(handle=handle), empty_env, store)

        mock_state = _get_mock_state(store)
        assert mock_state.statuses["test"] == SessionStatus.STOPPED

    @pytest.mark.asyncio
    async def test_multiple_sessions(self, mock_handlers, empty_env, empty_store, sample_config):
        """Multiple sessions operate independently."""
        # Configure different scripts
        configure_mock_session(
            empty_store,
            "session1",
            MockSessionScript([(SessionStatus.DONE, "Done 1")]),
        )
        configure_mock_session(
            empty_store,
            "session2",
            MockSessionScript([
                (SessionStatus.RUNNING, "Working..."),
                (SessionStatus.BLOCKED, "Blocked..."),
            ]),
        )

        store = empty_store
        launch_handler = mock_handlers[LaunchEffect]
        monitor_handler = mock_handlers[MonitorEffect]

        # Launch both
        handle1, store = await launch_handler(
            LaunchEffect(session_name="session1", config=sample_config),
            empty_env,
            store,
        )
        handle2, store = await launch_handler(
            LaunchEffect(session_name="session2", config=sample_config),
            empty_env,
            store,
        )

        # Monitor both
        obs1, store = await monitor_handler(MonitorEffect(handle=handle1), empty_env, store)
        obs2, store = await monitor_handler(MonitorEffect(handle=handle2), empty_env, store)

        assert obs1.status == SessionStatus.DONE
        assert obs2.status == SessionStatus.RUNNING

        obs2b, store = await monitor_handler(MonitorEffect(handle=handle2), empty_env, store)
        assert obs2b.status == SessionStatus.BLOCKED
