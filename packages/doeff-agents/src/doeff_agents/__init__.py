"""doeff-agents: Agent session management for coding agents in tmux.

This package provides both:
1. Imperative API (session.py) - direct function calls with context managers
2. Effects API (effects/, programs.py) - composable effects for doeff integration

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

# Effect handler imports (for doeff_vm integration)
# Handler imports
from .handlers import (
    AGENT_SESSIONS_KEY,
    MOCK_AGENT_STATE_KEY,
    AgentHandler,
    MockAgentHandler,
    MockAgentState,
    MockSessionScript,
    TmuxAgentHandler,
    agent_effectful_handler,
    agent_effectful_handlers,
    configure_mock_session,
    dispatch_effect,
    make_scheduled_handler,
    make_typed_handler,
    mock_agent_handler,
    mock_agent_handlers,
    mock_handlers,
    production_handlers,
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
    # Effect handlers (doeff_vm protocol)
    "agent_effectful_handler",
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
    "make_scheduled_handler",
    "make_typed_handler",
    "mock_agent_handler",
    "mock_agent_handlers",
    "mock_handlers",
    "monitor_once",
    "monitor_session",
    "monitor_until_terminal",
    "production_handlers",
    "quick_agent",
    "register_adapter",
    "run_agent_to_completion",
    "send_message",
    "session_scope",
    "stop_session",
    "wait_and_monitor",
    "with_session",
]
