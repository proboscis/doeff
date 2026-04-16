"""doeff-agents: Agent session management for coding agents in tmux.

Effects API Example:
    from doeff_agents.effects import Launch, Monitor, Stop
    from doeff_agents.adapters.base import AgentType

    handle = yield Launch("my-session", agent_type=AgentType.CLAUDE, work_dir=Path.cwd(), prompt="hello")
    obs = yield Monitor(handle)
    yield Stop(handle)
"""

from .adapters.base import AgentAdapter, AgentType, InjectionMethod, LaunchConfig, LaunchParams

from .effects import (
    AgentError,
    AgentNotAvailableError,
    Capture,
    CaptureEffect,
    ClaudeLaunchEffect,
    Launch,
    LaunchEffect,
    Monitor,
    MonitorEffect,
    Observation,
    Send,
    SendEffect,
    SessionAlreadyExistsError,
    SessionHandle,
    SessionNotFoundError,
    Sleep,
    SleepEffect,
    Stop,
    StopEffect,
)

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
    get_mock_agent_state,
    make_scheduled_handler,
    mock_agent_handler,
    mock_agent_handlers,
    mock_handlers,
    production_handlers,
)
from .monitor import MonitorState, OnStatusChange, SessionStatus

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
from .session_backend import SessionBackend
from .tmux import (
    SessionConfig,
    SessionInfo,
    TmuxError,
    TmuxNotAvailableError,
    TmuxSessionBackend,
)
