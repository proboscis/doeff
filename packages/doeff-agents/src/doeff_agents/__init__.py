"""doeff-agents: Agent session management for coding agents in tmux.

This package provides both:
1. Imperative API (session.py) - direct function calls with context managers
2. Effects API (effects.py, programs.py) - composable effects for doeff integration

Imperative API Example:
    from doeff_agents import session_scope, monitor_session, LaunchConfig, AgentType

    config = LaunchConfig(AgentType.CLAUDE, Path.cwd(), "Hello")
    with session_scope("my-session", config) as session:
        while not session.is_terminal:
            monitor_session(session)
            time.sleep(1)

Effects API Example:
    from doeff_agents.effects import Launch, Monitor, Stop, SessionHandle
    from doeff_agents.programs import run_agent_to_completion
    from doeff_agents.handlers import MockAgentHandler

    # Use fine-grained effects
    handle = yield Launch("my-session", config)
    obs = yield Monitor(handle)
    yield Stop(handle)

    # Or use high-level programs
    result = yield from run_agent_to_completion("my-session", config)
"""

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

# Effects API imports
from .effects import (
    # Types
    SessionHandle,
    Observation,
    # Effects
    LaunchEffect,
    MonitorEffect,
    CaptureEffect,
    SendEffect,
    StopEffect,
    SleepEffect,
    WithSessionEffect,
    # Constructors
    Launch,
    Monitor,
    Capture,
    Send,
    Stop,
    Sleep,
    # Errors (re-export effect-specific errors)
    AgentError,
    AgentNotAvailableError,
)

# Handler imports
from .handlers import (
    AgentHandler,
    TmuxAgentHandler,
    MockAgentHandler,
    MockSessionScript,
    dispatch_effect,
)

# Program imports
from .programs import (
    AgentResult,
    run_agent_to_completion,
    with_session,
    monitor_until_terminal,
    monitor_once,
    wait_and_monitor,
    quick_agent,
    interactive_session,
)

# CESK handler imports (for doeff integration)
from .cesk_handlers import (
    agent_effectful_handlers,
    mock_agent_handlers,
    MockSessionScript as CeskMockSessionScript,
    MockAgentState,
    configure_mock_session,
    AGENT_SESSIONS_KEY,
    MOCK_AGENT_STATE_KEY,
)

__all__ = [
    # Adapters
    "AgentAdapter",
    "AgentType",
    "InjectionMethod",
    "LaunchConfig",
    # Monitor
    "MonitorState",
    "OnStatusChange",
    "SessionStatus",
    # Session (imperative API)
    "AgentLaunchError",
    "AgentReadyTimeoutError",
    "AgentSession",
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
    # Tmux
    "SessionAlreadyExistsError",
    "SessionConfig",
    "SessionInfo",
    "SessionNotFoundError",
    "TmuxError",
    "TmuxNotAvailableError",
    # Effects API - Types
    "SessionHandle",
    "Observation",
    # Effects API - Effects
    "LaunchEffect",
    "MonitorEffect",
    "CaptureEffect",
    "SendEffect",
    "StopEffect",
    "SleepEffect",
    "WithSessionEffect",
    # Effects API - Constructors
    "Launch",
    "Monitor",
    "Capture",
    "Send",
    "Stop",
    "Sleep",
    # Effects API - Errors
    "AgentError",
    "AgentNotAvailableError",
    # Handlers
    "AgentHandler",
    "TmuxAgentHandler",
    "MockAgentHandler",
    "MockSessionScript",
    "dispatch_effect",
    # Programs
    "AgentResult",
    "run_agent_to_completion",
    "with_session",
    "monitor_until_terminal",
    "monitor_once",
    "wait_and_monitor",
    "quick_agent",
    "interactive_session",
    # CESK Handlers (doeff integration)
    "agent_effectful_handlers",
    "mock_agent_handlers",
    "CeskMockSessionScript",
    "MockAgentState",
    "configure_mock_session",
    "AGENT_SESSIONS_KEY",
    "MOCK_AGENT_STATE_KEY",
]
