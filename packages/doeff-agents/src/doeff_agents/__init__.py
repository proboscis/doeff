"""doeff-agents: Agent session management for coding agents in tmux."""

from .adapters.base import AgentAdapter, AgentType, InjectionMethod, LaunchConfig
from .monitor import MonitorState, OnStatusChange, SessionStatus
from .session import (
    AgentLaunchError,
    AgentReadyTimeoutError,
    AgentSession,
    async_monitor_session,
    async_session_scope,
    attach_session,
    capture_output,
    get_adapter,
    launch_session,
    monitor_session,
    register_adapter,
    send_message,
    session_scope,
    stop_session,
)
from .tmux import (
    SessionAlreadyExistsError,
    SessionConfig,
    SessionInfo,
    SessionNotFoundError,
    TmuxError,
    TmuxNotAvailableError,
)

__all__ = [
    "AgentAdapter",
    "AgentLaunchError",
    "AgentReadyTimeoutError",
    "AgentSession",
    "AgentType",
    "InjectionMethod",
    "LaunchConfig",
    "MonitorState",
    "OnStatusChange",
    "SessionAlreadyExistsError",
    "SessionConfig",
    "SessionInfo",
    "SessionNotFoundError",
    "SessionStatus",
    "TmuxError",
    "TmuxNotAvailableError",
    "async_monitor_session",
    "async_session_scope",
    "attach_session",
    "capture_output",
    "get_adapter",
    "launch_session",
    "monitor_session",
    "register_adapter",
    "send_message",
    "session_scope",
    "stop_session",
]
