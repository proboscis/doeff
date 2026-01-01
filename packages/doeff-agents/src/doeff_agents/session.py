"""High-level session management API with context managers and async support."""

import asyncio
import re
import shlex
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import tmux
from .adapters.base import AgentAdapter, AgentType, InjectionMethod, LaunchConfig
from .adapters.claude import ClaudeAdapter
from .adapters.codex import CodexAdapter
from .adapters.gemini import GeminiAdapter
from .monitor import (
    MonitorState,
    OnStatusChange,
    SessionStatus,
    detect_pr_url,
    detect_status,
    hash_content,
    is_waiting_for_input,
)


class AgentLaunchError(Exception):
    """Error during agent launch."""


class AgentReadyTimeoutError(AgentLaunchError):
    """Agent did not become ready within timeout."""


@dataclass
class AgentSession:
    """Represents a running agent session."""

    session_name: str
    pane_id: str  # For reliable tmux targeting
    agent_type: AgentType
    work_dir: Path
    status: SessionStatus = SessionStatus.PENDING
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _monitor_state: MonitorState = field(default_factory=MonitorState)
    _adapter: AgentAdapter | None = field(default=None, repr=False)

    @property
    def is_terminal(self) -> bool:
        """Check if session is in a terminal state."""
        return self.status in (
            SessionStatus.DONE,
            SessionStatus.FAILED,
            SessionStatus.EXITED,
            SessionStatus.STOPPED,
        )


# Adapter registry (extensible)
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


def launch_session(
    session_name: str,
    config: LaunchConfig,
    *,
    ready_timeout: float = 30.0,
) -> AgentSession:
    """Launch a new agent session in tmux.

    Args:
        session_name: Name for the tmux session
        config: Launch configuration
        ready_timeout: Timeout for agent to be ready (for TMUX injection)

    Returns:
        AgentSession tracking the running session

    Raises:
        AgentLaunchError: If agent CLI is not available
        AgentReadyTimeoutError: If agent doesn't become ready within timeout
        tmux.SessionAlreadyExistsError: If session already exists
    """
    adapter = get_adapter(config.agent_type)

    if not adapter.is_available():
        raise AgentLaunchError(f"{config.agent_type.value} CLI is not available")

    # Create tmux session (raises SessionAlreadyExistsError if exists)
    tmux_config = tmux.SessionConfig(
        session_name=session_name,
        work_dir=config.work_dir,
    )
    session_info = tmux.new_session(tmux_config)

    # Build and send command
    argv = adapter.launch_command(config)
    command = shlex.join(argv)

    if adapter.injection_method == InjectionMethod.ARG:
        # Command includes prompt - send directly
        tmux.send_keys(session_info.pane_id, command)
    else:
        # Send command first, wait for ready, then send prompt
        tmux.send_keys(session_info.pane_id, command)
        if adapter.ready_pattern and not _wait_for_ready(
            session_info.pane_id, adapter.ready_pattern, ready_timeout
        ):
            # Clean up on timeout
            tmux.kill_session(session_name)
            raise AgentReadyTimeoutError(f"Agent did not become ready within {ready_timeout}s")
        if config.prompt:
            tmux.send_keys(session_info.pane_id, config.prompt)

    return AgentSession(
        session_name=session_name,
        pane_id=session_info.pane_id,
        agent_type=config.agent_type,
        work_dir=config.work_dir,
        status=SessionStatus.BOOTING,
        _adapter=adapter,
    )


@contextmanager
def session_scope(
    session_name: str,
    config: LaunchConfig,
    *,
    ready_timeout: float = 30.0,
) -> Iterator[AgentSession]:
    """Context manager for agent session lifecycle.

    Ensures session is stopped on exit (even on exception).

    Usage:
        with session_scope("my-session", config) as session:
            while not session.is_terminal:
                monitor_session(session)
                time.sleep(1)
    """
    session = launch_session(session_name, config, ready_timeout=ready_timeout)
    try:
        yield session
    finally:
        stop_session(session)


def monitor_session(
    session: AgentSession,
    *,
    on_status_change: OnStatusChange | None = None,
    on_pr_detected: Callable[[str], None] | None = None,
) -> SessionStatus | None:
    """Check session status and update if changed.

    Args:
        session: The session to monitor
        on_status_change: Callback(old_status, new_status, output)
        on_pr_detected: Callback(pr_url) when PR URL is detected

    Returns:
        New status if changed, None otherwise
    """
    if not tmux.has_session(session.session_name):
        old_status = session.status
        session.status = SessionStatus.EXITED
        if on_status_change and old_status != SessionStatus.EXITED:
            on_status_change(old_status, SessionStatus.EXITED, None)
        return SessionStatus.EXITED

    # Use pane_id for reliable targeting
    output = tmux.capture_pane(session.pane_id)

    # Get skip_lines from adapter if available
    skip_lines = 5
    if session._adapter and hasattr(session._adapter, "status_bar_lines"):
        skip_lines = session._adapter.status_bar_lines

    content_hash = hash_content(output, skip_lines)
    output_changed = content_hash != session._monitor_state.output_hash
    has_prompt = is_waiting_for_input(output)

    if output_changed:
        session._monitor_state.output_hash = content_hash
        session._monitor_state.last_output = output
        session._monitor_state.last_output_at = datetime.now(timezone.utc)

    # Detect PR URL
    if on_pr_detected and not session._monitor_state.pr_url:
        pr_url = detect_pr_url(output)
        if pr_url:
            session._monitor_state.pr_url = pr_url
            on_pr_detected(pr_url)

    new_status = detect_status(output, session._monitor_state, output_changed, has_prompt)

    if new_status and new_status != session.status:
        old_status = session.status
        session.status = new_status
        if on_status_change:
            on_status_change(old_status, new_status, output)
        return new_status

    return None


def send_message(session: AgentSession, message: str, *, enter: bool = True) -> None:
    """Send a message to the agent session.

    Args:
        session: The session to send to
        message: The message text
        enter: If True, press Enter after message
    """
    if not tmux.has_session(session.session_name):
        raise RuntimeError(f"Session {session.session_name} does not exist")
    tmux.send_keys(session.pane_id, message, enter=enter)


def capture_output(session: AgentSession, lines: int = 100) -> str:
    """Capture current pane output."""
    return tmux.capture_pane(session.pane_id, lines)


def stop_session(session: AgentSession) -> None:
    """Stop (kill) an agent session."""
    if tmux.has_session(session.session_name):
        tmux.kill_session(session.session_name)
    session.status = SessionStatus.STOPPED


def attach_session(session: AgentSession) -> None:
    """Attach to a session (blocks until detached)."""
    tmux.attach_session(session.session_name)


def _wait_for_ready(target: str, pattern: str, timeout: float) -> bool:
    """Wait for agent to be ready for input."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        output = tmux.capture_pane(target, 50)
        if re.search(pattern, output):
            return True
        time.sleep(0.2)
    return False


# ============================================================================
# Async API
# ============================================================================


async def async_monitor_session(
    session: AgentSession,
    *,
    poll_interval: float = 1.0,
    on_status_change: OnStatusChange | None = None,
    on_pr_detected: Callable[[str], None] | None = None,
) -> SessionStatus:
    """Async monitor that yields when status changes.

    Args:
        session: The session to monitor
        poll_interval: Seconds between status checks
        on_status_change: Callback for status changes
        on_pr_detected: Callback for PR detection

    Returns:
        Final terminal status
    """
    while not session.is_terminal:
        monitor_session(session, on_status_change=on_status_change, on_pr_detected=on_pr_detected)
        await asyncio.sleep(poll_interval)
    return session.status


@asynccontextmanager
async def async_session_scope(
    session_name: str,
    config: LaunchConfig,
    *,
    ready_timeout: float = 30.0,
) -> AsyncIterator[AgentSession]:
    """Async context manager for agent session lifecycle."""
    session = launch_session(session_name, config, ready_timeout=ready_timeout)
    try:
        yield session
    finally:
        stop_session(session)
