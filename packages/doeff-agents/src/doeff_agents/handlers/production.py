"""Production effect handler backed by tmux."""

from __future__ import annotations

import re
import shlex
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from doeff_agents import tmux
from doeff_agents.adapters.base import AgentAdapter, AgentType, InjectionMethod
from doeff_agents.adapters.claude import ClaudeAdapter
from doeff_agents.adapters.codex import CodexAdapter
from doeff_agents.adapters.gemini import GeminiAdapter
from doeff_agents.effects import (
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
from doeff_agents.monitor import (
    MonitorState,
    SessionStatus,
    detect_pr_url,
    detect_status,
    hash_content,
    is_waiting_for_input,
)


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


@dataclass
class SessionState:
    """Mutable state for a session (internal to handler)."""

    handle: SessionHandle
    adapter: AgentAdapter
    monitor_state: MonitorState = field(default_factory=MonitorState)
    status: SessionStatus = SessionStatus.BOOTING
    pr_url: str | None = None


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

        if tmux.has_session(effect.session_name):
            raise SessionAlreadyExistsError(f"Session {effect.session_name} already exists")

        tmux_config = tmux.SessionConfig(
            session_name=effect.session_name,
            work_dir=config.work_dir,
        )
        session_info = tmux.new_session(tmux_config)

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

        self._sessions[effect.session_name] = SessionState(handle=handle, adapter=adapter)
        return handle

    def handle_monitor(self, effect: MonitorEffect) -> Observation:
        """Check session status and return observation."""
        handle = effect.handle
        state = self._sessions.get(handle.session_name)

        if state is None:
            if not tmux.has_session(handle.session_name):
                return Observation(status=SessionStatus.EXITED)
            state = SessionState(handle=handle, adapter=get_adapter(handle.agent_type))
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


__all__ = [
    "AgentHandler",
    "SessionState",
    "TmuxAgentHandler",
    "get_adapter",
    "register_adapter",
]
