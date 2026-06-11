"""Effect handlers for agent session management."""


from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any

from doeff_time import sync_time_handler

from doeff import Effect, Resume, WithHandler, do, run
from doeff.mcp import McpToolDef
from doeff_agents.agentd_client import LazyAgentdClient
from doeff_agents.effects import (
    AgentEffect,
    AttachAgentSessionEffect,
    AwaitResultEffect,
    CancelAgentSessionEffect,
    CaptureEffect,
    ClaudeLaunchEffect,
    CleanupAgentSessionEffect,
    FollowUpEffect,
    GetAgentSessionEffect,
    LaunchEffect,
    LaunchSessionEffect,
    ListAgentSessionsEffect,
    MonitorEffect,
    ObserveAgentSessionEffect,
    ReleaseSessionEffect,
    SendEffect,
    StopEffect,
    StopSessionEffect,
)
from doeff_agents.runtime import ClaudeRuntimePolicy
from doeff_agents.session_store import AgentSessionRepository

from .daemon import AgentdSessionClient, DaemonAgentHandler
from .production import AgentHandler, SessionState, TmuxAgentHandler, get_adapter, register_adapter
from .testing import (
    MockAgentHandler,
    MockAgentState,
    MockSessionScript,
    ScenarioAgentHandler,
    ScenarioStep,
)

# Keys kept for compatibility with persisted metadata naming.
AGENT_SESSIONS_KEY = "__agent_sessions__"
MOCK_AGENT_STATE_KEY = "__mock_agent_state__"

# Supported effect types for doeff-agents handlers.
AGENT_EFFECT_TYPES = (
    AgentEffect,
    LaunchSessionEffect,
    AwaitResultEffect,
    FollowUpEffect,
    StopSessionEffect,
    ReleaseSessionEffect,
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


def dispatch_effect(handler: AgentHandler, effect: Any) -> Any:  # noqa: PLR0912 - baseline cleanup keeps existing control flow unchanged
    """Dispatch an effect to the appropriate handler method."""
    result = None
    if isinstance(effect, AgentEffect):
        result = handler.handle_agent(effect)
    elif isinstance(effect, LaunchSessionEffect):
        result = handler.handle_launch_session(effect)
    elif isinstance(effect, AwaitResultEffect):
        result = handler.handle_await_result(effect)
    elif isinstance(effect, FollowUpEffect):
        result = handler.handle_follow_up(effect)
    elif isinstance(effect, StopSessionEffect):
        result = handler.handle_stop_session(effect)
    elif isinstance(effect, ReleaseSessionEffect):
        result = handler.handle_release_session(effect)
    elif isinstance(effect, LaunchEffect):
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
ProtocolHandler = Callable[..., Any]


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
    """Wrap an AgentHandler object with the Hy defhandler boundary."""
    return _hy_effectful_module().agent_handler_defhandler(agent_handler)


def _make_ask_agent_protocol_handler(
    *,
    session_repository: AgentSessionRepository | None = None,
) -> ProtocolHandler:
    return _hy_effectful_module().tmux_agent_defhandler(
        session_repository=session_repository,
    )


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


def _hy_effectful_module():
    import hy  # noqa: F401  # activate Hy import hook

    return import_module("doeff_agents.handlers.effectful")


def agent_effectful_handler(
    *,
    session_repository: AgentSessionRepository | None = None,
) -> ProtocolHandler:
    """Return the real tmux handler as a Hy defhandler.

    The session backend is resolved through Ask(SessionBackend), so
    deployment-specific tmux paths are injected by the doeff environment.
    """
    return _hy_effectful_module().tmux_agent_defhandler(
        session_repository=session_repository,
    )


def mock_agent_handler() -> ProtocolHandler:
    """Return the mock testing handler as a Hy defhandler."""
    return _hy_effectful_module().agent_handler_defhandler(_mock_effect_handler)


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


def daemon_agent_handler(
    *,
    socket_path: str | Path | None = None,
    db_path: str | Path | None = None,
    daemon_bin: str | Path | None = None,
    client: AgentdSessionClient | None = None,
    claude_runtime_policy: ClaudeRuntimePolicy | None = None,
    max_running: int = 10,
) -> ProtocolHandler:
    """Return the daemon-backed agent handler as a Hy defhandler."""
    active_client = client
    if active_client is None:
        active_client = LazyAgentdClient(
            socket_path=socket_path,
            db_path=db_path,
            daemon_bin=daemon_bin,
            max_running=max_running,
        )
    agent_handler = DaemonAgentHandler(
        client=active_client,
        claude_runtime_policy=claude_runtime_policy,
    )
    return _hy_effectful_module().agent_handler_defhandler(agent_handler)


def daemon_agent_handlers(
    *,
    socket_path: str | Path | None = None,
    db_path: str | Path | None = None,
    daemon_bin: str | Path | None = None,
    client: AgentdSessionClient | None = None,
    claude_runtime_policy: ClaudeRuntimePolicy | None = None,
    time_handler: ProtocolHandler | None = None,
    max_running: int = 10,
) -> tuple[ProtocolHandler, ...]:
    """Return standard handlers for doeff-agentd-backed workflows."""
    return (
        time_handler or sync_time_handler(),
        daemon_agent_handler(
            socket_path=socket_path,
            db_path=db_path,
            daemon_bin=daemon_bin,
            client=client,
            claude_runtime_policy=claude_runtime_policy,
            max_running=max_running,
        ),
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
    "AgentdSessionClient",
    "AgentSessionRepository",
    "DaemonAgentHandler",
    "MockAgentHandler",
    "MockAgentState",
    "MOCK_AGENT_STATE_KEY",
    "MockSessionScript",
    "ProtocolHandler",
    "ScenarioAgentHandler",
    "ScenarioStep",
    "SessionState",
    "TmuxAgentHandler",
    "agent_effectful_handler",
    "agent_effectful_handlers",
    "codex_agent_handler",
    "configure_mock_session",
    "daemon_agent_handler",
    "daemon_agent_handlers",
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
