"""doeff-agents: Agent session management for coding agents in tmux.

The package root intentionally keeps imports lazy. The imperative session
transport API can run without importing the doeff VM, which matters on hosts
that only need tmux-backed process supervision.
"""

from importlib import import_module
from typing import Any

_LAZY_EXPORTS = {
    "AgentAdapter": ".adapters.base",
    "AgentSessionLifecycle": ".adapters.base",
    "AgentType": ".adapters.base",
    "InjectionMethod": ".adapters.base",
    "LaunchConfig": ".adapters.base",
    "LaunchParams": ".adapters.base",
    "AgentdClient": ".agentd_client",
    "AgentdClientError": ".agentd_client",
    "AgentdPaths": ".agentd_client",
    "AgentdProtocolError": ".agentd_client",
    "LazyAgentdClient": ".agentd_client",
    "default_agentd_paths": ".agentd_client",
    "ensure_agentd": ".agentd_client",
    "AgentError": ".effects",
    "AgentNotAvailableError": ".effects",
    "AgentSessionQuery": ".effects",
    "AgentSessionSnapshot": ".effects",
    "AttachAgentSession": ".effects",
    "AttachAgentSessionEffect": ".effects",
    "CancelAgentSession": ".effects",
    "CancelAgentSessionEffect": ".effects",
    "Capture": ".effects",
    "CaptureEffect": ".effects",
    "ClaudeLaunchEffect": ".effects",
    "CleanupAgentSession": ".effects",
    "CleanupAgentSessionEffect": ".effects",
    "GetAgentSession": ".effects",
    "GetAgentSessionEffect": ".effects",
    "Launch": ".effects",
    "LaunchEffect": ".effects",
    "ListAgentSessions": ".effects",
    "ListAgentSessionsEffect": ".effects",
    "Monitor": ".effects",
    "MonitorEffect": ".effects",
    "ObserveAgentSession": ".effects",
    "ObserveAgentSessionEffect": ".effects",
    "Observation": ".effects",
    "Send": ".effects",
    "SendEffect": ".effects",
    "SessionAlreadyExistsError": ".effects",
    "SessionHandle": ".effects",
    "SessionNotFoundError": ".effects",
    "Stop": ".effects",
    "StopEffect": ".effects",
    "AGENT_SESSIONS_KEY": ".handlers",
    "MOCK_AGENT_STATE_KEY": ".handlers",
    "AgentHandler": ".handlers",
    "AgentdSessionClient": ".handlers",
    "DaemonAgentHandler": ".handlers",
    "MockAgentHandler": ".handlers",
    "MockAgentState": ".handlers",
    "MockSessionScript": ".handlers",
    "TmuxAgentHandler": ".handlers",
    "agent_effectful_handler": ".handlers",
    "agent_effectful_handlers": ".handlers",
    "codex_agent_handler": ".handlers",
    "configure_mock_session": ".handlers",
    "daemon_agent_handler": ".handlers",
    "daemon_agent_handlers": ".handlers",
    "dispatch_effect": ".handlers",
    "get_mock_agent_state": ".handlers",
    "make_scheduled_handler": ".handlers",
    "mock_agent_handler": ".handlers",
    "mock_agent_handlers": ".handlers",
    "mock_handlers": ".handlers",
    "production_handlers": ".handlers",
    "MonitorState": ".monitor",
    "OnStatusChange": ".monitor",
    "SessionStatus": ".monitor",
    "AgentResult": ".programs",
    "interactive_session": ".programs",
    "monitor_agent_to_completion": ".programs",
    "monitor_once": ".programs",
    "monitor_until_terminal": ".programs",
    "quick_agent": ".programs",
    "run_agent_to_completion": ".programs",
    "wait_and_monitor": ".programs",
    "wait_agent_session": ".programs",
    "with_session": ".programs",
    "ClaudeRuntimePolicy": ".runtime",
    "AgentSessionEvent": ".session_store",
    "AgentSessionRepository": ".session_store",
    "InMemoryAgentSessionRepository": ".session_store",
    "JsonlAgentSessionRepository": ".session_store",
    "AgentLaunchError": ".session",
    "AgentReadyTimeoutError": ".session",
    "AgentSession": ".session",
    "async_monitor_session": ".session",
    "async_session_scope": ".session",
    "attach_session": ".session",
    "capture_output": ".session",
    "get_adapter": ".session",
    "launch_session": ".session",
    "monitor_session": ".session",
    "register_adapter": ".session",
    "send_message": ".session",
    "session_scope": ".session",
    "stop_session": ".session",
    "SessionBackend": ".session_backend",
    "SessionConfig": ".tmux",
    "SessionInfo": ".tmux",
    "TmuxError": ".tmux",
    "TmuxNotAvailableError": ".tmux",
    "TmuxSessionBackend": ".tmux",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *_LAZY_EXPORTS])
