"""CESK-compatible effect handlers for doeff integration.

This module provides handlers that integrate with doeff's CESK interpreter,
following the HandlerContext-based handler protocol.

Usage with CESK runtime:
    from doeff import AsyncRuntime
    from doeff_agents.cesk_handlers import agent_effectful_handlers

    runtime = AsyncRuntime(handlers=agent_effectful_handlers())
    result = await runtime.run(my_program)

For testing without real tmux:
    from doeff_agents.cesk_handlers import mock_agent_handlers

    runtime = AsyncRuntime(handlers=mock_agent_handlers())
    result = await runtime.run(my_program)
"""

from __future__ import annotations

import asyncio
import re
import shlex
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from doeff.cesk.frames import ContinueValue, FrameResult

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

if TYPE_CHECKING:
    from doeff.cesk.runtime.context import HandlerContext
    from doeff.cesk.types import Store


# =============================================================================
# Adapter Registry
# =============================================================================

_adapters: dict[AgentType, type[AgentAdapter]] = {
    AgentType.CLAUDE: ClaudeAdapter,  # type: ignore[dict-item]
    AgentType.CODEX: CodexAdapter,  # type: ignore[dict-item]
    AgentType.GEMINI: GeminiAdapter,  # type: ignore[dict-item]
}


def get_adapter(agent_type: AgentType) -> AgentAdapter:
    """Get the adapter for an agent type."""
    adapter_class = _adapters.get(agent_type)
    if adapter_class is None:
        raise ValueError(f"No adapter registered for: {agent_type}")
    return adapter_class()


# =============================================================================
# Session State (stored in CESK Store)
# =============================================================================

# Key in Store for agent session state
AGENT_SESSIONS_KEY = "__agent_sessions__"


@dataclass
class SessionState:
    """Mutable state for a session (stored in CESK Store)."""

    handle: SessionHandle
    adapter: AgentAdapter
    monitor_state: MonitorState = field(default_factory=MonitorState)
    status: SessionStatus = SessionStatus.BOOTING
    pr_url: str | None = None


def _get_sessions(store: Store) -> dict[str, SessionState]:
    """Get agent sessions from store, creating if needed."""
    sessions = store.get(AGENT_SESSIONS_KEY)
    if sessions is None:
        sessions = {}
        store[AGENT_SESSIONS_KEY] = sessions
    return sessions


# =============================================================================
# Effectful Handlers (Async) - Real Tmux Implementation
# =============================================================================


def _handle_launch(
    effect: LaunchEffect,
    ctx: HandlerContext,
) -> FrameResult:
    """Handle Launch effect - creates real tmux session."""
    store = ctx.store
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
        tmux.send_keys(session_info.pane_id, command)
    else:
        tmux.send_keys(session_info.pane_id, command)
        if adapter.ready_pattern:
            ready = _wait_for_ready_sync(
                session_info.pane_id,
                adapter.ready_pattern,
                effect.ready_timeout,
            )
            if not ready:
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

    # Store session state
    sessions = _get_sessions(store)
    sessions[effect.session_name] = SessionState(
        handle=handle,
        adapter=adapter,
    )

    return ContinueValue(
        value=handle,
        env=ctx.task_state.env,
        store=store,
        k=ctx.task_state.kontinuation,
    )


def _wait_for_ready_sync(target: str, pattern: str, timeout: float) -> bool:
    """Wait for agent to be ready for input (sync version)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        output = tmux.capture_pane(target, 50)
        if re.search(pattern, output):
            return True
        time.sleep(0.2)
    return False


def _handle_monitor(
    effect: MonitorEffect,
    ctx: HandlerContext,
) -> FrameResult:
    """Handle Monitor effect - checks session status."""
    store = ctx.store
    handle = effect.handle
    sessions = _get_sessions(store)
    state = sessions.get(handle.session_name)

    if state is None:
        # Session not tracked - check if it exists
        if not tmux.has_session(handle.session_name):
            return ContinueValue(
                value=Observation(status=SessionStatus.EXITED),
                env=ctx.task_state.env,
                store=store,
                k=ctx.task_state.kontinuation,
            )
        # Create minimal state for monitoring
        state = SessionState(
            handle=handle,
            adapter=get_adapter(handle.agent_type),
        )
        sessions[handle.session_name] = state

    if not tmux.has_session(handle.session_name):
        state.status = SessionStatus.EXITED
        return ContinueValue(
            value=Observation(status=SessionStatus.EXITED),
            env=ctx.task_state.env,
            store=store,
            k=ctx.task_state.kontinuation,
        )

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

    return ContinueValue(
        value=Observation(
            status=state.status,
            output_changed=output_changed,
            pr_url=pr_url,
            output_snippet=output[-500:] if output else None,
        ),
        env=ctx.task_state.env,
        store=store,
        k=ctx.task_state.kontinuation,
    )


def _handle_capture(
    effect: CaptureEffect,
    ctx: HandlerContext,
) -> FrameResult:
    """Handle Capture effect - captures pane output."""
    handle = effect.handle
    if not tmux.has_session(handle.session_name):
        raise SessionNotFoundError(f"Session {handle.session_name} does not exist")
    output = tmux.capture_pane(handle.pane_id, effect.lines)
    return ContinueValue(
        value=output,
        env=ctx.task_state.env,
        store=ctx.store,
        k=ctx.task_state.kontinuation,
    )


def _handle_send(
    effect: SendEffect,
    ctx: HandlerContext,
) -> FrameResult:
    """Handle Send effect - sends message to session."""
    handle = effect.handle
    if not tmux.has_session(handle.session_name):
        raise SessionNotFoundError(f"Session {handle.session_name} does not exist")
    tmux.send_keys(
        handle.pane_id,
        effect.message,
        literal=effect.literal,
        enter=effect.enter,
    )
    return ContinueValue(
        value=None,
        env=ctx.task_state.env,
        store=ctx.store,
        k=ctx.task_state.kontinuation,
    )


def _handle_stop(
    effect: StopEffect,
    ctx: HandlerContext,
) -> FrameResult:
    """Handle Stop effect - stops session."""
    store = ctx.store
    handle = effect.handle
    if tmux.has_session(handle.session_name):
        tmux.kill_session(handle.session_name)

    # Update state if tracked
    sessions = _get_sessions(store)
    state = sessions.get(handle.session_name)
    if state:
        state.status = SessionStatus.STOPPED

    return ContinueValue(
        value=None,
        env=ctx.task_state.env,
        store=store,
        k=ctx.task_state.kontinuation,
    )


def _handle_sleep(
    effect: SleepEffect,
    ctx: HandlerContext,
) -> FrameResult:
    """Handle Sleep effect - sleeps for duration."""
    time.sleep(effect.seconds)
    return ContinueValue(
        value=None,
        env=ctx.task_state.env,
        store=ctx.store,
        k=ctx.task_state.kontinuation,
    )


def agent_effectful_handlers() -> dict[type, Any]:
    """Create effectful handler registry for agent effects.

    Returns a dict suitable for passing to `run(effectful_handlers=...)`.
    """
    return {
        LaunchEffect: _handle_launch,
        MonitorEffect: _handle_monitor,
        CaptureEffect: _handle_capture,
        SendEffect: _handle_send,
        StopEffect: _handle_stop,
        SleepEffect: _handle_sleep,
    }


# =============================================================================
# Mock Handlers for Testing
# =============================================================================


@dataclass
class MockSessionScript:
    """Script for mock session behavior."""

    observations: list[tuple[SessionStatus, str]] = field(default_factory=list)
    _index: int = field(default=0, repr=False)

    def next_observation(self) -> tuple[SessionStatus, str]:
        """Get next observation from script."""
        if self._index >= len(self.observations):
            return (SessionStatus.DONE, "")
        obs = self.observations[self._index]
        self._index += 1
        return obs


@dataclass
class MockAgentState:
    """Mock state for testing."""

    scripts: dict[str, MockSessionScript] = field(default_factory=dict)
    handles: dict[str, SessionHandle] = field(default_factory=dict)
    statuses: dict[str, SessionStatus] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    sends: list[tuple[str, str]] = field(default_factory=list)
    sleep_calls: list[float] = field(default_factory=list)
    next_pane_id: int = 0


# Key in Store for mock state
MOCK_AGENT_STATE_KEY = "__mock_agent_state__"


def _get_mock_state(store: Store) -> MockAgentState:
    """Get mock state from store, creating if needed."""
    state = store.get(MOCK_AGENT_STATE_KEY)
    if state is None:
        state = MockAgentState()
        store[MOCK_AGENT_STATE_KEY] = state
    return state


def configure_mock_session(
    store: Store,
    session_name: str,
    script: MockSessionScript | None = None,
    initial_output: str = "",
) -> None:
    """Configure a mock session before running."""
    state = _get_mock_state(store)
    if script:
        state.scripts[session_name] = script
    state.outputs[session_name] = initial_output
    state.statuses[session_name] = SessionStatus.BOOTING


def _mock_handle_launch(
    effect: LaunchEffect,
    ctx: HandlerContext,
) -> FrameResult:
    """Mock Launch effect."""
    store = ctx.store
    state = _get_mock_state(store)

    if effect.session_name in state.handles:
        raise SessionAlreadyExistsError(f"Session {effect.session_name} already exists")

    pane_id = f"%mock{state.next_pane_id}"
    state.next_pane_id += 1

    handle = SessionHandle(
        session_name=effect.session_name,
        pane_id=pane_id,
        agent_type=effect.config.agent_type,
        work_dir=effect.config.work_dir,
    )
    state.handles[effect.session_name] = handle
    state.statuses[effect.session_name] = SessionStatus.BOOTING
    state.outputs.setdefault(effect.session_name, "")

    return ContinueValue(
        value=handle,
        env=ctx.task_state.env,
        store=store,
        k=ctx.task_state.kontinuation,
    )


def _mock_handle_monitor(
    effect: MonitorEffect,
    ctx: HandlerContext,
) -> FrameResult:
    """Mock Monitor effect."""
    store = ctx.store
    state = _get_mock_state(store)
    session_name = effect.handle.session_name

    if session_name not in state.handles:
        return ContinueValue(
            value=Observation(status=SessionStatus.EXITED),
            env=ctx.task_state.env,
            store=store,
            k=ctx.task_state.kontinuation,
        )

    script = state.scripts.get(session_name)
    if script:
        status, output = script.next_observation()
        state.statuses[session_name] = status
        state.outputs[session_name] = output
        return ContinueValue(
            value=Observation(
                status=status,
                output_changed=True,
                output_snippet=output[-500:] if output else None,
            ),
            env=ctx.task_state.env,
            store=store,
            k=ctx.task_state.kontinuation,
        )

    # Default behavior without script
    return ContinueValue(
        value=Observation(
            status=state.statuses.get(session_name, SessionStatus.RUNNING),
            output_changed=False,
        ),
        env=ctx.task_state.env,
        store=store,
        k=ctx.task_state.kontinuation,
    )


def _mock_handle_capture(
    effect: CaptureEffect,
    ctx: HandlerContext,
) -> FrameResult:
    """Mock Capture effect."""
    store = ctx.store
    state = _get_mock_state(store)
    session_name = effect.handle.session_name
    if session_name not in state.handles:
        raise SessionNotFoundError(f"Session {session_name} does not exist")
    return ContinueValue(
        value=state.outputs.get(session_name, ""),
        env=ctx.task_state.env,
        store=store,
        k=ctx.task_state.kontinuation,
    )


def _mock_handle_send(
    effect: SendEffect,
    ctx: HandlerContext,
) -> FrameResult:
    """Mock Send effect."""
    store = ctx.store
    state = _get_mock_state(store)
    session_name = effect.handle.session_name
    if session_name not in state.handles:
        raise SessionNotFoundError(f"Session {session_name} does not exist")
    state.sends.append((session_name, effect.message))
    return ContinueValue(
        value=None,
        env=ctx.task_state.env,
        store=store,
        k=ctx.task_state.kontinuation,
    )


def _mock_handle_stop(
    effect: StopEffect,
    ctx: HandlerContext,
) -> FrameResult:
    """Mock Stop effect."""
    store = ctx.store
    state = _get_mock_state(store)
    session_name = effect.handle.session_name
    if session_name in state.handles:
        state.statuses[session_name] = SessionStatus.STOPPED
    return ContinueValue(
        value=None,
        env=ctx.task_state.env,
        store=store,
        k=ctx.task_state.kontinuation,
    )


def _mock_handle_sleep(
    effect: SleepEffect,
    ctx: HandlerContext,
) -> FrameResult:
    """Mock Sleep effect - records but doesn't wait."""
    store = ctx.store
    state = _get_mock_state(store)
    state.sleep_calls.append(effect.seconds)
    return ContinueValue(
        value=None,
        env=ctx.task_state.env,
        store=store,
        k=ctx.task_state.kontinuation,
    )


def mock_agent_handlers() -> dict[type, Any]:
    """Create mock handler registry for testing.

    Returns a dict suitable for passing to `run(effectful_handlers=...)`.
    """
    return {
        LaunchEffect: _mock_handle_launch,
        MonitorEffect: _mock_handle_monitor,
        CaptureEffect: _mock_handle_capture,
        SendEffect: _mock_handle_send,
        StopEffect: _mock_handle_stop,
        SleepEffect: _mock_handle_sleep,
    }


__all__ = [  # noqa: RUF022 - grouped by category
    # Real handlers
    "agent_effectful_handlers",
    # Mock handlers
    "mock_agent_handlers",
    "MockSessionScript",
    "MockAgentState",
    "configure_mock_session",
    # Store keys
    "AGENT_SESSIONS_KEY",
    "MOCK_AGENT_STATE_KEY",
]
