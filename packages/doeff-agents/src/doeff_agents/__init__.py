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
    AgentTaskSpec,
    Capture,
    CaptureEffect,
    ClaudeLaunchEffect,
    ExpectedArtifact,
    # Constructors
    Launch,
    LaunchClaude,
    # Effects
    LaunchEffect,
    LaunchTask,
    LaunchTaskEffect,
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
    WorkspaceFile,
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
from .runtime import ClaudeRuntimePolicy, lower_task_launch_to_claude
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
from .session_backend import SessionBackend
from .tmux import (
    SessionAlreadyExistsError,
    SessionConfig,
    SessionInfo,
    SessionNotFoundError,
    TmuxError,
    TmuxNotAvailableError,
    TmuxSessionBackend,
)

__all__ = sorted([
    "AGENT_SESSIONS_KEY",
    "MOCK_AGENT_STATE_KEY",
    "AgentAdapter",
    "AgentError",
    "AgentHandler",
    "AgentLaunchError",
    "AgentNotAvailableError",
    "AgentReadyTimeoutError",
    "AgentResult",
    "AgentSession",
    "AgentTaskSpec",
    "AgentType",
    "Capture",
    "CaptureEffect",
    "ClaudeLaunchEffect",
    "ClaudeRuntimePolicy",
    "ExpectedArtifact",
    "InjectionMethod",
    "Launch",
    "LaunchClaude",
    "LaunchConfig",
    "LaunchEffect",
    "LaunchTask",
    "LaunchTaskEffect",
    "MockAgentHandler",
    "MockAgentState",
    "MockSessionScript",
    "Monitor",
    "MonitorEffect",
    "MonitorState",
    "Observation",
    "OnStatusChange",
    "Send",
    "SendEffect",
    "SessionAlreadyExistsError",
    "SessionBackend",
    "SessionConfig",
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
    "TmuxSessionBackend",
    "WithSessionEffect",
    "WorkspaceFile",
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
    "lower_task_launch_to_claude",
    "make_scheduled_handler",
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
])
