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

# CESK handler imports (for doeff integration)
from .cesk_handlers import (
    AGENT_SESSIONS_KEY,
    MOCK_AGENT_STATE_KEY,
    MockAgentState,
    agent_effectful_handlers,
    configure_mock_session,
    mock_agent_handlers,
)
from .cesk_handlers import (
    MockSessionScript as CeskMockSessionScript,
)

# Effects API imports
from .effects import (
    # Errors (re-export effect-specific errors)
    AgentError,
    AgentNotAvailableError,
    Capture,
    CaptureEffect,
    # Constructors
    Launch,
    # Effects
    LaunchEffect,
    Monitor,
    MonitorEffect,
    Observation,
    Send,
    SendEffect,
    # Types
    SessionHandle,
    Sleep,
    SleepEffect,
    Stop,
    StopEffect,
    WithSessionEffect,
)

# Handler imports
from .handlers import (
    AgentHandler,
    MockAgentHandler,
    MockSessionScript,
    TmuxAgentHandler,
    dispatch_effect,
)
from .monitor import MonitorState, OnStatusChange, SessionStatus

# Program imports
from .programs import (
    AgentResult,
    interactive_session,
    monitor_once,
    monitor_until_terminal,
    quick_agent,
    run_agent_to_completion,
    wait_and_monitor,
    with_session,
)
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
    "AGENT_SESSIONS_KEY",
    "MOCK_AGENT_STATE_KEY",
    # Adapters
    "AgentAdapter",
    # Effects API - Errors
    "AgentError",
    # Handlers
    "AgentHandler",
    # Session (imperative API)
    "AgentLaunchError",
    "AgentNotAvailableError",
    "AgentReadyTimeoutError",
    # Programs
    "AgentResult",
    "AgentSession",
    "AgentType",
    "Capture",
    "CaptureEffect",
    "CeskMockSessionScript",
    "InjectionMethod",
    # Effects API - Constructors
    "Launch",
    "LaunchConfig",
    # Effects API - Effects
    "LaunchEffect",
    "MockAgentHandler",
    "MockAgentState",
    "MockSessionScript",
    "Monitor",
    "MonitorEffect",
    # Monitor
    "MonitorState",
    "Observation",
    "OnStatusChange",
    "Send",
    "SendEffect",
    # Tmux
    "SessionAlreadyExistsError",
    "SessionConfig",
    # Effects API - Types
    "SessionHandle",
    "SessionInfo",
    "SessionNotFoundError",
    "SessionStatus",
    "Sleep",
    "SleepEffect",
    "Stop",
    "StopEffect",
    "TmuxAgentHandler",
    "TmuxError",
    "TmuxNotAvailableError",
    "WithSessionEffect",
    # CESK Handlers (doeff integration)
    "agent_effectful_handlers",
    "async_monitor_session",
    "async_session_scope",
    "attach_session",
    "capture_output",
    "configure_mock_session",
    "dispatch_effect",
    "get_adapter",
    "interactive_session",
    "launch_session",
    "mock_agent_handlers",
    "monitor_once",
    "monitor_session",
    "monitor_until_terminal",
    "quick_agent",
    "register_adapter",
    "run_agent_to_completion",
    "send_message",
    "session_scope",
    "stop_session",
    "wait_and_monitor",
    "with_session",
]
