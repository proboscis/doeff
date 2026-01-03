"""Effect handlers for agent session management.

This module provides handlers that interpret agent effects:
- TmuxAgentHandler: Real handler using tmux
- MockAgentHandler: Mock handler for testing with scriptable state

Handler Design:
- Handlers maintain session state keyed by SessionHandle
- Effects are pure data; handlers perform side effects
- Mock handler enables deterministic testing without tmux
"""

from __future__ import annotations

import re
import shlex
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import tmux
from .adapters.base import AgentAdapter, AgentType, InjectionMethod
from .adapters.claude import ClaudeAdapter
from .adapters.codex import CodexAdapter
from .adapters.gemini import GeminiAdapter
from .effects import (
    AgentNotAvailableError,
    AgentReadyTimeoutError,
    CaptureEffect,
    LaunchEffect,
    MonitorEffect,
    Observation,
    SendEffect,
    SessionAlreadyExistsError,
    SessionHandle,
    SessionNotFoundError,
    SleepEffect,
    StopEffect,
)
from .monitor import (
    MonitorState,
    SessionStatus,
    detect_pr_url,
    detect_status,
    hash_content,
    is_waiting_for_input,
)

# =============================================================================
# Handler Protocol
# =============================================================================


class AgentHandler(ABC):
    """Abstract handler for agent effects."""

    @abstractmethod
    def handle_launch(self, effect: LaunchEffect) -> SessionHandle:
        """Handle Launch effect."""

    @abstractmethod
    def handle_monitor(self, effect: MonitorEffect) -> Observation:
        """Handle Monitor effect."""

    @abstractmethod
    def handle_capture(self, effect: CaptureEffect) -> str:
        """Handle Capture effect."""

    @abstractmethod
    def handle_send(self, effect: SendEffect) -> None:
        """Handle Send effect."""

    @abstractmethod
    def handle_stop(self, effect: StopEffect) -> None:
        """Handle Stop effect."""

    @abstractmethod
    def handle_sleep(self, effect: SleepEffect) -> None:
        """Handle Sleep effect."""


# =============================================================================
# Session State (internal to handlers)
# =============================================================================


@dataclass
class SessionState:
    """Mutable state for a session (internal to handler)."""

    handle: SessionHandle
    adapter: AgentAdapter
    monitor_state: MonitorState = field(default_factory=MonitorState)
    status: SessionStatus = SessionStatus.BOOTING
    pr_url: str | None = None


# =============================================================================
# Adapter Registry
# =============================================================================

_adapters: dict[AgentType, type[AgentAdapter]] = {
    AgentType.CLAUDE: ClaudeAdapter,  # type: ignore[dict-item]
    AgentType.CODEX: CodexAdapter,  # type: ignore[dict-item]
    AgentType.GEMINI: GeminiAdapter,  # type: ignore[dict-item]
}


def register_adapter(agent_type: AgentType, adapter_class: type[AgentAdapter]) -> None:
    """Register a custom adapter."""
    _adapters[agent_type] = adapter_class


def get_adapter(agent_type: AgentType) -> AgentAdapter:
    """Get the adapter for an agent type."""
    adapter_class = _adapters.get(agent_type)
    if adapter_class is None:
        raise ValueError(f"No adapter registered for: {agent_type}")
    return adapter_class()


# =============================================================================
# Real Handler (Tmux)
# =============================================================================


class TmuxAgentHandler(AgentHandler):
    """Handler that executes effects using real tmux sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def handle_launch(self, effect: LaunchEffect) -> SessionHandle:
        """Launch a new agent session in tmux."""
        config = effect.config
        adapter = get_adapter(config.agent_type)

        if not adapter.is_available():
            raise AgentNotAvailableError(f"{config.agent_type.value} CLI is not available")

        # Check for existing session
        if tmux.has_session(effect.session_name):
            raise SessionAlreadyExistsError(f"Session {effect.session_name} already exists")

        # Create tmux session
        tmux_config = tmux.SessionConfig(
            session_name=effect.session_name,
            work_dir=config.work_dir,
        )
        session_info = tmux.new_session(tmux_config)

        # Build and send command
        argv = adapter.launch_command(config)
        command = shlex.join(argv)

        if adapter.injection_method == InjectionMethod.ARG:
            tmux.send_keys(session_info.pane_id, command, literal=False)
        else:
            tmux.send_keys(session_info.pane_id, command, literal=False)
            if adapter.ready_pattern and not self._wait_for_ready(
                session_info.pane_id, adapter.ready_pattern, effect.ready_timeout
            ):
                tmux.kill_session(effect.session_name)
                raise AgentReadyTimeoutError(
                    f"Agent did not become ready within {effect.ready_timeout}s"
                )
            if config.prompt:
                tmux.send_keys(session_info.pane_id, config.prompt)

        handle = SessionHandle(
            session_name=effect.session_name,
            pane_id=session_info.pane_id,
            agent_type=config.agent_type,
            work_dir=config.work_dir,
        )

        self._sessions[effect.session_name] = SessionState(
            handle=handle,
            adapter=adapter,
        )

        return handle

    def handle_monitor(self, effect: MonitorEffect) -> Observation:
        """Check session status and return observation."""
        handle = effect.handle
        state = self._sessions.get(handle.session_name)

        if state is None:
            # Session not tracked by this handler
            if not tmux.has_session(handle.session_name):
                return Observation(status=SessionStatus.EXITED)
            # Create minimal state for monitoring
            state = SessionState(
                handle=handle,
                adapter=get_adapter(handle.agent_type),
            )
            self._sessions[handle.session_name] = state

        if not tmux.has_session(handle.session_name):
            state.status = SessionStatus.EXITED
            return Observation(status=SessionStatus.EXITED)

        output = tmux.capture_pane(handle.pane_id)

        skip_lines = 5
        if hasattr(state.adapter, "status_bar_lines"):
            skip_lines = state.adapter.status_bar_lines

        content_hash = hash_content(output, skip_lines)
        output_changed = content_hash != state.monitor_state.output_hash
        has_prompt = is_waiting_for_input(output)

        if output_changed:
            state.monitor_state.output_hash = content_hash
            state.monitor_state.last_output = output
            state.monitor_state.last_output_at = datetime.now(timezone.utc)

        # Detect PR URL
        pr_url = None
        if not state.pr_url:
            detected_url = detect_pr_url(output)
            if detected_url:
                state.pr_url = detected_url
                pr_url = detected_url

        new_status = detect_status(output, state.monitor_state, output_changed, has_prompt)
        if new_status:
            state.status = new_status

        return Observation(
            status=state.status,
            output_changed=output_changed,
            pr_url=pr_url,
            output_snippet=output[-500:] if output else None,
        )

    def handle_capture(self, effect: CaptureEffect) -> str:
        """Capture pane output."""
        handle = effect.handle
        if not tmux.has_session(handle.session_name):
            raise SessionNotFoundError(f"Session {handle.session_name} does not exist")
        return tmux.capture_pane(handle.pane_id, effect.lines)

    def handle_send(self, effect: SendEffect) -> None:
        """Send message to session."""
        handle = effect.handle
        if not tmux.has_session(handle.session_name):
            raise SessionNotFoundError(f"Session {handle.session_name} does not exist")
        tmux.send_keys(
            handle.pane_id,
            effect.message,
            literal=effect.literal,
            enter=effect.enter,
        )

    def handle_stop(self, effect: StopEffect) -> None:
        """Stop session."""
        handle = effect.handle
        if tmux.has_session(handle.session_name):
            tmux.kill_session(handle.session_name)
        # Update state if tracked
        state = self._sessions.get(handle.session_name)
        if state:
            state.status = SessionStatus.STOPPED

    def handle_sleep(self, effect: SleepEffect) -> None:
        """Sleep for duration."""
        time.sleep(effect.seconds)

    def _wait_for_ready(self, target: str, pattern: str, timeout: float) -> bool:
        """Wait for agent to be ready for input."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            output = tmux.capture_pane(target, 50)
            if re.search(pattern, output):
                return True
            time.sleep(0.2)
        return False


# =============================================================================
# Mock Handler (for testing)
# =============================================================================


@dataclass
class MockSessionScript:
    """Script for mock session behavior.

    Allows testing with deterministic, scriptable state transitions.
    """

    # List of (status, output) tuples, consumed in order
    observations: list[tuple[SessionStatus, str]] = field(default_factory=list)
    # Current index in observations
    _index: int = field(default=0, repr=False)

    def next_observation(self) -> tuple[SessionStatus, str]:
        """Get next observation from script."""
        if self._index >= len(self.observations):
            # Default to DONE when script exhausted
            return (SessionStatus.DONE, "")
        obs = self.observations[self._index]
        self._index += 1
        return obs


class MockAgentHandler(AgentHandler):
    """Mock handler for testing without tmux.

    Features:
    - Scriptable state transitions
    - Captured sends for verification
    - Instant sleep (no real delays)
    - Deterministic behavior
    """

    def __init__(self) -> None:
        self._sessions: dict[str, MockSessionScript] = {}
        self._handles: dict[str, SessionHandle] = {}
        self._statuses: dict[str, SessionStatus] = {}
        self._outputs: dict[str, str] = {}
        self._sends: list[tuple[str, str]] = []  # (session_name, message)
        self._sleep_calls: list[float] = []
        self._next_pane_id: int = 0

    def configure_session(
        self,
        session_name: str,
        script: MockSessionScript | None = None,
        initial_output: str = "",
    ) -> None:
        """Pre-configure a session for testing."""
        if script:
            self._sessions[session_name] = script
        self._outputs[session_name] = initial_output
        self._statuses[session_name] = SessionStatus.BOOTING

    def handle_launch(self, effect: LaunchEffect) -> SessionHandle:
        """Create mock session."""
        if effect.session_name in self._handles:
            raise SessionAlreadyExistsError(f"Session {effect.session_name} already exists")

        pane_id = f"%mock{self._next_pane_id}"
        self._next_pane_id += 1

        handle = SessionHandle(
            session_name=effect.session_name,
            pane_id=pane_id,
            agent_type=effect.config.agent_type,
            work_dir=effect.config.work_dir,
        )
        self._handles[effect.session_name] = handle
        self._statuses[effect.session_name] = SessionStatus.BOOTING
        self._outputs.setdefault(effect.session_name, "")

        return handle

    def handle_monitor(self, effect: MonitorEffect) -> Observation:
        """Return next observation from script."""
        session_name = effect.handle.session_name

        if session_name not in self._handles:
            return Observation(status=SessionStatus.EXITED)

        script = self._sessions.get(session_name)
        if script:
            status, output = script.next_observation()
            self._statuses[session_name] = status
            self._outputs[session_name] = output
            return Observation(
                status=status,
                output_changed=True,
                output_snippet=output[-500:] if output else None,
            )

        # Default behavior without script
        return Observation(
            status=self._statuses.get(session_name, SessionStatus.RUNNING),
            output_changed=False,
        )

    def handle_capture(self, effect: CaptureEffect) -> str:
        """Return captured output."""
        session_name = effect.handle.session_name
        if session_name not in self._handles:
            raise SessionNotFoundError(f"Session {session_name} does not exist")
        return self._outputs.get(session_name, "")

    def handle_send(self, effect: SendEffect) -> None:
        """Record sent message."""
        session_name = effect.handle.session_name
        if session_name not in self._handles:
            raise SessionNotFoundError(f"Session {session_name} does not exist")
        self._sends.append((session_name, effect.message))

    def handle_stop(self, effect: StopEffect) -> None:
        """Mark session as stopped."""
        session_name = effect.handle.session_name
        if session_name in self._handles:
            self._statuses[session_name] = SessionStatus.STOPPED

    def handle_sleep(self, effect: SleepEffect) -> None:
        """Record sleep call (no actual delay)."""
        self._sleep_calls.append(effect.seconds)

    # Test helpers

    @property
    def sent_messages(self) -> list[tuple[str, str]]:
        """Get all sent messages as (session_name, message) tuples."""
        return list(self._sends)

    @property
    def total_sleep_time(self) -> float:
        """Get total sleep time requested."""
        return sum(self._sleep_calls)


# =============================================================================
# Handler dispatch
# =============================================================================


def dispatch_effect(handler: AgentHandler, effect: Any) -> Any:
    """Dispatch an effect to the appropriate handler method.

    Returns the result of handling the effect.
    """
    if isinstance(effect, LaunchEffect):
        return handler.handle_launch(effect)
    if isinstance(effect, MonitorEffect):
        return handler.handle_monitor(effect)
    if isinstance(effect, CaptureEffect):
        return handler.handle_capture(effect)
    if isinstance(effect, SendEffect):
        return handler.handle_send(effect)
    if isinstance(effect, StopEffect):
        return handler.handle_stop(effect)
    if isinstance(effect, SleepEffect):
        return handler.handle_sleep(effect)
    raise TypeError(f"Unknown effect type: {type(effect)}")


__all__ = [
    "AgentHandler",
    "MockAgentHandler",
    "MockSessionScript",
    "SessionState",
    "TmuxAgentHandler",
    "dispatch_effect",
    "get_adapter",
    "register_adapter",
]
