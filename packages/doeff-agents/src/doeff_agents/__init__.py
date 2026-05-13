"""doeff-agents: Agent session management for coding agents in tmux.

The package root intentionally keeps imports lazy. The imperative session
transport API can run without importing the doeff VM, which matters on hosts
that only need tmux-backed process supervision.
"""

from importlib import import_module
from typing import Any

_LAZY_EXPORTS = {
    "AgentAdapter": ".adapters.base",
    "AgentType": ".adapters.base",
    "InjectionMethod": ".adapters.base",
    "LaunchConfig": ".adapters.base",
    "LaunchParams": ".adapters.base",
    "AgentError": ".effects",
    "AgentNotAvailableError": ".effects",
    "Capture": ".effects",
    "CaptureEffect": ".effects",
    "ClaudeLaunchEffect": ".effects",
    "Launch": ".effects",
    "LaunchEffect": ".effects",
    "Monitor": ".effects",
    "MonitorEffect": ".effects",
    "Observation": ".effects",
    "Send": ".effects",
    "SendEffect": ".effects",
    "SessionAlreadyExistsError": ".effects",
    "SessionHandle": ".effects",
    "SessionNotFoundError": ".effects",
    "Sleep": ".effects",
    "SleepEffect": ".effects",
    "Stop": ".effects",
    "StopEffect": ".effects",
    "AGENT_SESSIONS_KEY": ".handlers",
    "MOCK_AGENT_STATE_KEY": ".handlers",
    "AgentHandler": ".handlers",
    "MockAgentHandler": ".handlers",
    "MockAgentState": ".handlers",
    "MockSessionScript": ".handlers",
    "TmuxAgentHandler": ".handlers",
    "agent_effectful_handler": ".handlers",
    "agent_effectful_handlers": ".handlers",
    "codex_agent_handler": ".handlers",
    "configure_mock_session": ".handlers",
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
    "monitor_once": ".programs",
    "monitor_until_terminal": ".programs",
    "quick_agent": ".programs",
    "run_agent_to_completion": ".programs",
    "wait_and_monitor": ".programs",
    "with_session": ".programs",
    "ClaudeRuntimePolicy": ".runtime",
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
