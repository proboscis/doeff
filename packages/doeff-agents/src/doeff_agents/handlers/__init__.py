"""Effect handlers for agent session management."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from doeff import Delegate, Resume
from doeff_agents.effects import (
    CaptureEffect,
    LaunchEffect,
    MonitorEffect,
    SendEffect,
    SleepEffect,
    StopEffect,
)

from .production import AgentHandler, SessionState, TmuxAgentHandler, get_adapter, register_adapter
from .testing import MockAgentHandler, MockAgentState, MockSessionScript

# Keys kept for compatibility with persisted metadata naming.
AGENT_SESSIONS_KEY = "__agent_sessions__"
MOCK_AGENT_STATE_KEY = "__mock_agent_state__"

# Supported effect types for doeff-agents handlers.
AGENT_EFFECT_TYPES = (
    LaunchEffect,
    MonitorEffect,
    CaptureEffect,
    SendEffect,
    StopEffect,
    SleepEffect,
)


def dispatch_effect(handler: AgentHandler, effect: Any) -> Any:
    """Dispatch an effect to the appropriate handler method."""
    if isinstance(effect, LaunchEffect):
        return handler.handle_launch(effect)
    if isinstance(effect, MonitorEffect):
        return handler.handle_monitor(effect)
    if isinstance(effect, CaptureEffect):
        return handler.handle_capture(effect)
    if isinstance(effect, SendEffect):
        return handler.handle_send(effect)
    if isinstance(effect, StopEffect):
        return handler.handle_stop(effect)
    if isinstance(effect, SleepEffect):
        return handler.handle_sleep(effect)
    raise TypeError(f"Unknown effect type: {type(effect)}")


SimpleHandler = Callable[[Any], Any]
ProtocolHandler = Callable[[Any, Any], Any]


def make_scheduled_handler(handler: SimpleHandler) -> ProtocolHandler:
    """Wrap a plain `(effect) -> value` callable into `(effect, k) -> DoExpr`."""

    def scheduled_handler(effect: Any, k):
        return (yield Resume(k, handler(effect)))

    return scheduled_handler


def make_typed_handler(effect_type: type[Any], handler: ProtocolHandler) -> ProtocolHandler:
    """Restrict a protocol handler to one effect type and delegate otherwise."""

    def typed_handler(effect: Any, k):
        if isinstance(effect, effect_type):
            result = handler(effect, k)
            if inspect.isgenerator(result):
                return (yield from result)
            return result
        yield Delegate()

    return typed_handler


def _make_protocol_handler(agent_handler: AgentHandler) -> ProtocolHandler:
    """Convert an AgentHandler object to doeff_vm handler protocol."""

    scheduled_dispatch = make_scheduled_handler(lambda effect: dispatch_effect(agent_handler, effect))

    def protocol_handler(effect: Any, k):
        if not isinstance(effect, AGENT_EFFECT_TYPES):
            yield Delegate()
            return
        return (yield from scheduled_dispatch(effect, k))

    return protocol_handler


_tmux_effect_handler = TmuxAgentHandler()
_mock_effect_handler = MockAgentHandler()
_tmux_protocol_handler = _make_protocol_handler(_tmux_effect_handler)
_mock_protocol_handler = _make_protocol_handler(_mock_effect_handler)


def agent_effectful_handler() -> ProtocolHandler:
    """Return the real tmux handler in `(effect, k) -> DoExpr` form."""
    return _tmux_protocol_handler


def mock_agent_handler() -> ProtocolHandler:
    """Return the mock testing handler in `(effect, k) -> DoExpr` form."""
    return _mock_protocol_handler


def agent_effectful_handlers() -> tuple[ProtocolHandler, ...]:
    """Compatibility shim returning protocol handlers for real tmux effects."""
    return (agent_effectful_handler(),)


def mock_agent_handlers() -> tuple[ProtocolHandler, ...]:
    """Compatibility shim returning protocol handlers for mock effects."""
    return (mock_agent_handler(),)


def production_handlers() -> tuple[ProtocolHandler, ...]:
    """Canonical handler tuple for production (tmux-backed) execution."""
    return agent_effectful_handlers()


def mock_handlers() -> tuple[ProtocolHandler, ...]:
    """Canonical handler tuple for mock execution in tests."""
    return mock_agent_handlers()


def configure_mock_session(
    session_name: str,
    script: MockSessionScript | None = None,
    initial_output: str = "",
) -> None:
    """Configure a mock session before program execution."""
    _mock_effect_handler.configure_session(session_name, script, initial_output)


def get_mock_agent_state() -> MockAgentState:
    """Return current mock state snapshot."""
    return _mock_effect_handler.snapshot()


__all__ = [  # noqa: RUF022 - grouped by category for readability
    "AGENT_EFFECT_TYPES",
    "AGENT_SESSIONS_KEY",
    "AgentHandler",
    "MockAgentHandler",
    "MockAgentState",
    "MOCK_AGENT_STATE_KEY",
    "MockSessionScript",
    "ProtocolHandler",
    "SessionState",
    "TmuxAgentHandler",
    "agent_effectful_handler",
    "agent_effectful_handlers",
    "configure_mock_session",
    "dispatch_effect",
    "get_adapter",
    "get_mock_agent_state",
    "make_scheduled_handler",
    "make_typed_handler",
    "mock_agent_handler",
    "mock_agent_handlers",
    "mock_handlers",
    "production_handlers",
    "register_adapter",
]
