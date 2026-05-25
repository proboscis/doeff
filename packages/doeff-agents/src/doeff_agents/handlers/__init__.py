"""Effect handlers for agent session management."""


from collections.abc import Callable
from importlib import import_module
from typing import Any

from doeff_time import sync_time_handler

from doeff import Ask, Effect, GetHandlers, Pass, Resume, WithHandler, do, run
from doeff.mcp import McpToolDef
from doeff_agents.effects import (
    AttachAgentSessionEffect,
    CancelAgentSessionEffect,
    CaptureEffect,
    ClaudeLaunchEffect,
    CleanupAgentSessionEffect,
    GetAgentSessionEffect,
    LaunchEffect,
    ListAgentSessionsEffect,
    MonitorEffect,
    ObserveAgentSessionEffect,
    SendEffect,
    StopEffect,
)
from doeff_agents.session_backend import SessionBackend
from doeff_agents.session_store import AgentSessionRepository

from .production import AgentHandler, SessionState, TmuxAgentHandler, get_adapter, register_adapter
from .testing import MockAgentHandler, MockAgentState, MockSessionScript

# Keys kept for compatibility with persisted metadata naming.
AGENT_SESSIONS_KEY = "__agent_sessions__"
MOCK_AGENT_STATE_KEY = "__mock_agent_state__"

# Supported effect types for doeff-agents handlers.
AGENT_EFFECT_TYPES = (
    LaunchEffect,
    ClaudeLaunchEffect,
    MonitorEffect,
    CaptureEffect,
    SendEffect,
    StopEffect,
    GetAgentSessionEffect,
    ListAgentSessionsEffect,
    ObserveAgentSessionEffect,
    AttachAgentSessionEffect,
    CancelAgentSessionEffect,
    CleanupAgentSessionEffect,
)


def dispatch_effect(handler: AgentHandler, effect: Any) -> Any:
    """Dispatch an effect to the appropriate handler method."""
    result = None
    if isinstance(effect, LaunchEffect):
        result = handler.handle_launch(effect)
    elif isinstance(effect, ClaudeLaunchEffect):
        result = handler.handle_claude_launch(effect)
    elif isinstance(effect, MonitorEffect):
        result = handler.handle_monitor(effect)
    elif isinstance(effect, CaptureEffect):
        result = handler.handle_capture(effect)
    elif isinstance(effect, SendEffect):
        result = handler.handle_send(effect)
    elif isinstance(effect, StopEffect):
        result = handler.handle_stop(effect)
    elif isinstance(effect, GetAgentSessionEffect):
        result = handler.handle_get_session(effect)
    elif isinstance(effect, ListAgentSessionsEffect):
        result = handler.handle_list_sessions(effect)
    elif isinstance(effect, ObserveAgentSessionEffect):
        result = handler.handle_observe_session(effect)
    elif isinstance(effect, AttachAgentSessionEffect):
        result = handler.handle_attach_session(effect)
    elif isinstance(effect, CancelAgentSessionEffect):
        result = handler.handle_cancel_session(effect)
    elif isinstance(effect, CleanupAgentSessionEffect):
        result = handler.handle_cleanup_session(effect)
    return result


SimpleHandler = Callable[[Any], Any]
ProtocolHandler = Callable[[Effect, Any], Any]


def make_scheduled_handler(handler: SimpleHandler) -> ProtocolHandler:
    """Wrap a plain `(effect) -> value` callable into `(effect, k) -> DoExpr`."""

    @do
    def scheduled_handler(effect: Effect, k: Any):
        return (yield Resume(k, handler(effect)))

    return scheduled_handler


def _make_run_tool(handlers: list) -> Callable[[McpToolDef, dict], Any]:
    """Create a run_tool closure that executes tool programs with captured handlers.

    Each MCP tool call:
      1. Builds a DoExpr program from tool.handler(*args)
      2. Wraps it with the captured handler stack via WithHandler
      3. Runs it via doeff.run()
    """

    def run_tool(tool: McpToolDef, arguments: dict) -> Any:
        args = [arguments.get(name) for name in tool.param_names()]
        program = tool.handler(*args)
        for h in handlers:
            program = WithHandler(h, program)
        return run(program)

    return run_tool


def _make_protocol_handler(agent_handler: AgentHandler) -> ProtocolHandler:
    """Convert an AgentHandler object to doeff_vm handler protocol.

    DEPRECATED: legacy path for TmuxAgentHandler/MockAgentHandler OOP dispatch.
    New code should use claude_resolver_handler + claude_handler instead.
    """

    scheduled_dispatch = make_scheduled_handler(
        lambda effect: dispatch_effect(agent_handler, effect)
    )

    @do
    def protocol_handler(effect: Effect, k: Any):
        if not isinstance(effect, AGENT_EFFECT_TYPES):
            yield Pass(effect, k)
            return None

        # MCP-aware launch: capture handler stack and pass run_tool.
        # New LaunchEffect has .mcp_tools directly; old code with .config is no longer supported here.
        if (
            isinstance(effect, LaunchEffect)
            and getattr(effect, "mcp_tools", ())
            and hasattr(agent_handler, "handle_launch")
        ):
            handlers = yield GetHandlers(k)
            run_tool_fn = _make_run_tool(handlers)
            result = agent_handler.handle_launch(effect, run_tool=run_tool_fn)
            return (yield Resume(k, result))

        return (yield scheduled_dispatch(effect, k))

    return protocol_handler


def _make_ask_agent_protocol_handler(
    *,
    session_repository: AgentSessionRepository | None = None,
) -> ProtocolHandler:
    agent_handler_ref: dict[str, AgentHandler] = {}

    @do
    def protocol_handler(effect: Effect, k: Any):
        if not isinstance(effect, AGENT_EFFECT_TYPES):
            yield Pass(effect, k)
            return None

        agent_handler = agent_handler_ref.get("handler")
        if agent_handler is None:
            backend = yield Ask(SessionBackend)
            agent_handler = TmuxAgentHandler(
                backend=backend,
                session_repository=session_repository,
            )
            agent_handler_ref["handler"] = agent_handler

        if isinstance(effect, LaunchEffect) and getattr(effect, "mcp_tools", ()):
            handlers = yield GetHandlers(k)
            run_tool_fn = _make_run_tool(handlers)
            result = agent_handler.handle_launch(effect, run_tool=run_tool_fn)
            return (yield Resume(k, result))

        result = dispatch_effect(agent_handler, effect)
        return (yield Resume(k, result))

    return protocol_handler


# ---------------------------------------------------------------------------
# New handler composition — claude_resolver + claude_handler (Hy-based)
# ---------------------------------------------------------------------------

def claude_agent_handler(*, backend=None):
    """Claude agent handler (new Hy-based architecture).

    Catches LaunchEffect(agent_type=CLAUDE) directly — no resolver indirection.
    The resolver pattern was removed because GetHandlers(k) captures handlers
    from k's segment upward, and a resolver puts k inside itself, breaking the
    capture of domain handlers.

    Usage:
        handler = claude_agent_handler()
        wrapped = WithHandler(handler, program)
        run(wrapped)
    """
    import hy  # noqa: F401  # activate Hy import hook

    claude_handler = import_module("doeff_agents.handlers.claude").claude_handler
    return claude_handler(backend=backend)


def codex_agent_handler(*, backend=None):
    """Codex agent handler (Hy-based architecture)."""
    import hy  # noqa: F401  # activate Hy import hook

    codex_handler = import_module("doeff_agents.handlers.codex").codex_handler
    return codex_handler(backend=backend)


_mock_effect_handler = MockAgentHandler()
_mock_protocol_handler = _make_protocol_handler(_mock_effect_handler)


def agent_effectful_handler(
    *,
    session_repository: AgentSessionRepository | None = None,
) -> ProtocolHandler:
    """Return the real tmux handler in `(effect, k) -> DoExpr` form.

    The session backend is resolved through Ask(SessionBackend), so
    deployment-specific tmux paths are injected by the doeff environment.
    """
    return _make_ask_agent_protocol_handler(session_repository=session_repository)


def mock_agent_handler() -> ProtocolHandler:
    """Return the mock testing handler in `(effect, k) -> DoExpr` form."""
    return _mock_protocol_handler


def agent_effectful_handlers(
    *,
    time_handler: ProtocolHandler | None = None,
    session_repository: AgentSessionRepository | None = None,
) -> tuple[ProtocolHandler, ...]:
    """Return standard production handlers for real tmux agent workflows.

    High-level agent programs use ``doeff_time.Delay`` between monitor polls.
    Include a time handler by default so callers that use this convenience tuple
    do not accidentally leave Delay unhandled.
    """
    return (
        time_handler or sync_time_handler(),
        agent_effectful_handler(session_repository=session_repository),
    )


def mock_agent_handlers(
    *,
    time_handler: ProtocolHandler | None = None,
) -> tuple[ProtocolHandler, ...]:
    """Return standard mock handlers, including a no-op Delay handler."""
    noop_time_handler = sync_time_handler(sleep=lambda _seconds: None)
    return (time_handler or noop_time_handler, mock_agent_handler())


def production_handlers(
    *,
    session_repository: AgentSessionRepository | None = None,
) -> tuple[ProtocolHandler, ...]:
    """Canonical handler tuple for production (tmux-backed) execution."""
    return agent_effectful_handlers(session_repository=session_repository)


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
    "AgentSessionRepository",
    "MockAgentHandler",
    "MockAgentState",
    "MOCK_AGENT_STATE_KEY",
    "MockSessionScript",
    "ProtocolHandler",
    "SessionState",
    "TmuxAgentHandler",
    "agent_effectful_handler",
    "agent_effectful_handlers",
    "codex_agent_handler",
    "configure_mock_session",
    "dispatch_effect",
    "get_adapter",
    "get_mock_agent_state",
    "make_scheduled_handler",
    "mock_agent_handler",
    "mock_agent_handlers",
    "mock_handlers",
    "production_handlers",
    "register_adapter",
]
