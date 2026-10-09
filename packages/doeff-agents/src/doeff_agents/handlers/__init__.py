"""Effect handlers for agent session management."""


from collections.abc import Callable, Mapping
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

import hy  # noqa: F401  # activate Hy import hook for handler modules
from doeff_claude_code.values import BypassAll, PermissionPolicy
from doeff_time import sync_time_handler

if TYPE_CHECKING:
    from doeff_claude_code.fake import FakeClaudeWorld, FakeReply

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
from doeff_agents.handlers.daemon import AgentdSessionClient as AgentdSessionClient
from doeff_agents.handlers.daemon import DaemonAgentHandler as DaemonAgentHandler
from doeff_agents.handlers.production import AgentHandler as AgentHandler
from doeff_agents.handlers.production import SessionState as SessionState
from doeff_agents.handlers.production import TmuxAgentHandler as TmuxAgentHandler
from doeff_agents.handlers.production import get_adapter as get_adapter
from doeff_agents.handlers.production import register_adapter as register_adapter
from doeff_agents.handlers.testing import MockAgentHandler as MockAgentHandler
from doeff_agents.handlers.testing import MockAgentState as MockAgentState
from doeff_agents.handlers.testing import MockSessionScript as MockSessionScript
from doeff_agents.handlers.testing import ScenarioAgentHandler as ScenarioAgentHandler
from doeff_agents.handlers.testing import ScenarioStep as ScenarioStep
from doeff_agents.runtime import ClaudeRuntimePolicy, CodexRuntimePolicy
from doeff_agents.session_backend import SessionBackend
from doeff_agents.session_store import AgentSessionRepository as AgentSessionRepository

# The headless adapter's default permission policy: skip the CLI's permission
# checks (`BypassAll`) — the behavior every caller had before #3753.
_DEFAULT_CLAUDE_PERMISSION: PermissionPolicy = BypassAll()

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


def _agent_handler_defhandler(agent_handler: AgentHandler) -> Any:
    """Expose an AgentHandler object through the Hy defhandler boundary."""
    return _hy_effectful_module().agent_handler_defhandler(agent_handler)


def _tmux_agent_defhandler(
    *,
    session_repository: AgentSessionRepository | None = None,
) -> Any:
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
        wrapped = handler(program)
        run(wrapped)
    """
    import hy  # noqa: F401, F811  # activate Hy import hook (intentionally re-imported per call site)

    claude_handler = import_module("doeff_agents.handlers.claude").claude_handler
    return claude_handler(backend=backend)


def codex_agent_handler(*, backend=None):
    """Codex agent handler (Hy-based architecture)."""
    import hy  # noqa: F401, F811  # activate Hy import hook (intentionally re-imported per call site)

    codex_handler = import_module("doeff_agents.handlers.codex").codex_handler
    return codex_handler(backend=backend)


def _hy_headless_compose_module() -> Any:
    import hy  # noqa: F401, F811  # activate Hy import hook (intentionally re-imported per call site)

    return import_module("doeff_agents.handlers.headless_compose")


def headless_claude_agent_handlers(
    *,
    config_dir: str,
    env: dict[str, str],
    live_limit: int | None,
    credential_floor_seconds: float,
    settings: dict[str, Any] | None = None,
    cold_resume_prompt: str | None = None,
    command: tuple[str, ...] = ("claude",),
) -> list[Any]:
    """Headless Claude handlers: the public effects as an adapter onto doeff-claude-code.

    Returns ``[doeff-claude-code production handler, headless adapter]`` in
    ``with_handlers`` order (outer first). ``config_dir`` / ``env`` are the
    Claude home (credentials are placed in ``env`` by the composition root).
    ``live_limit`` is how many CLI processes the host keeps alive at once and
    ``credential_floor_seconds`` how long before a lent credential expires the
    host stops the process using it (both from the caller's declaration — no
    defaults; agora-redesign #3672 D2). ``live_limit=None`` declares no limit:
    for a caller that decides how many to keep alive by another measure (the
    machine's free memory), layer 2 then compares nothing and emits neither
    the over-limit log line nor ``ClaudeLiveLimitExceeded`` (agora-redesign
    #4282; ``None`` is passed through as is, and it is not a default — such a
    caller passes it explicitly).
    Install a doeff-time handler, a slog handler (the production handler
    emits CLI launch timing lines — agora-redesign #3605), a handler that
    answers ``ClaudeLiveLimitExceeded`` (the host: layer 2 never stops a CLI
    for the live limit — a launch past it is reported to the host instead,
    agora-redesign #4072 E1b; the type is re-exported from
    ``doeff_agents.handlers.headless_compose``) and the scheduler outside
    them. No session-host socket is opened (agora-redesign #604).
    """
    return _hy_headless_compose_module().headless_claude_handlers(
        config_dir,
        dict(env),
        settings,
        cold_resume_prompt,
        tuple(command),
        live_limit=live_limit,
        credential_floor_seconds=credential_floor_seconds,
    )


def fake_headless_claude_agent_handlers(
    *,
    responder: Any = None,
    config_dir: str = "fake-claude-home",
    world: Any = None,
    env: Mapping[str, str],
    settings: Mapping[str, object],
    permission: PermissionPolicy = _DEFAULT_CLAUDE_PERMISSION,
) -> list[Any]:
    """The same adapter over doeff-claude-code's fake handler (no process, no API).

    ``responder(text, memory) -> FakeReply`` scripts each turn
    (``doeff_agents.handlers.headless_compose.FakeReply``). Pass ``world``
    (a ``FakeClaudeWorld`` the caller keeps — e.g. ``world.restarted()`` for a
    new process over the same home) instead of ``responder``; exactly one.
    ``env`` / ``settings`` mean the same as for ``headless_claude_agent_handlers``
    (the home's process env as a str → str mapping and the CLI settings as a
    JSON mapping). Both are required (agora-redesign #3387): a caller's
    emulation passes what its production path decided, so the launch
    declaration the fake layer 2 receives carries them (agora-redesign #3327);
    a caller with nothing to declare passes empty mappings explicitly.
    ``permission`` is the launch declaration's permission policy, as for
    ``claude_agent_adapter_handler`` (default ``BypassAll()``).
    Returns ``[fake layer-2 handler, headless adapter]`` (outer first).
    """
    return _hy_headless_compose_module().fake_headless_claude_handlers(
        responder, config_dir, world, env=env, settings=settings, permission=permission
    )


def claude_agent_runtime_handlers(
    *,
    config_dir: str,
    env: dict[str, str],
    live_limit: int | None,
    credential_floor_seconds: float,
    settings: dict[str, Any] | None = None,
    cold_resume_prompt: str | None = None,
) -> list[Any]:
    """The agent runtime for Claude, as doeff-agents chooses it (agora-redesign #606).

    Callers that must not know the substrate (agora: "the agent runtime's
    substrate is the library's concern", operator decision O5) ask for the
    Claude agent runtime by this name; which substrate answers the public
    effects is decided here. Today it is the print-mode adapter over
    ``doeff-claude-code`` (the same pair as ``headless_claude_agent_handlers``).
    ``config_dir`` / ``env`` are the Claude home (credentials are placed by the
    composition root); ``live_limit`` / ``credential_floor_seconds`` as for
    ``headless_claude_agent_handlers``. Install a doeff-time handler, a slog
    handler, a ``ClaudeLiveLimitExceeded`` handler and the scheduler outside.
    """
    return headless_claude_agent_handlers(
        config_dir=config_dir,
        env=env,
        live_limit=live_limit,
        credential_floor_seconds=credential_floor_seconds,
        settings=settings,
        cold_resume_prompt=cold_resume_prompt,
    )


def fake_claude_agent_runtime_handlers(
    *,
    responder: Any = None,
    config_dir: str = "fake-claude-home",
    world: Any = None,
    env: Mapping[str, str],
    settings: Mapping[str, object],
    permission: PermissionPolicy = _DEFAULT_CLAUDE_PERMISSION,
) -> list[Any]:
    """The fake counterpart of ``claude_agent_runtime_handlers`` (no process, no API).

    ``responder(text, memory) -> FakeReply`` scripts each turn
    (``doeff_agents.handlers.headless_compose.FakeReply``), or ``world`` is a
    ``FakeClaudeWorld`` the caller keeps (exactly one of the two).
    ``env`` / ``settings`` are the same home env (str → str) and CLI settings
    (a JSON mapping) the production runtime takes; both are required — pass
    empty mappings explicitly when there is nothing to declare (agora-redesign
    #3387). They ride on the launch declaration only, as does ``permission``
    (the permission policy, default ``BypassAll()`` — agora-redesign #3753).
    Returns ``[fake layer-2 handler, headless adapter]`` (outer first).
    """
    return fake_headless_claude_agent_handlers(
        responder=responder,
        config_dir=config_dir,
        world=world,
        env=env,
        settings=settings,
        permission=permission,
    )


def claude_process_layer_handler(
    *,
    live_limit: int | None,
    credential_floor_seconds: float,
    command: tuple[str, ...] = ("claude",),
) -> Callable[..., object]:
    """Layer 2 alone: the production handler that keeps each conversation's CLI process alive across turns.

    The same handler ``claude_agent_runtime_handlers`` returns first. For a
    caller that places layer 2 and the adapter at different depths
    (agora-redesign #3507): layer 2 belongs to the foundation that owns real
    processes (an emulation answers layer 2 outside instead), the adapter sits
    next to the program. ``live_limit`` / ``credential_floor_seconds`` as for
    ``headless_claude_agent_handlers`` (agora-redesign #3672 D2). Install a
    doeff-time handler, the scheduler, a slog handler and a
    ``ClaudeLiveLimitExceeded`` handler outside it.
    """
    from doeff import run

    return run(
        _hy_headless_compose_module().claude_process_layer(
            tuple(command), live_limit, float(credential_floor_seconds)
        )
    )


def fake_claude_process_layer_handler(
    *,
    responder: Callable[[str, tuple[str, ...]], "FakeReply"] | None = None,
    world: "FakeClaudeWorld | None" = None,
) -> Callable[..., object]:
    """Layer 2 alone, fake (no process, no API) — the first of ``fake_claude_agent_runtime_handlers``.

    ``responder`` / ``world`` mean the same as for the fake pair (exactly one).
    """
    from doeff import run

    return run(_hy_headless_compose_module().fake_claude_process_layer(responder, world))


def claude_agent_adapter_handler(
    *,
    config_dir: str,
    env: Mapping[str, str],
    settings: Mapping[str, object],
    cold_resume_prompt: str | None = None,
    permission: PermissionPolicy = _DEFAULT_CLAUDE_PERMISSION,
) -> Callable[..., object]:
    """The headless adapter alone: the public effects onto layer-2 effects.

    The same adapter both pair entries return second — whether layer 2 is
    the production handler or the fake. ``config_dir`` / ``env`` are the
    Claude home, ``settings`` the CLI settings (a JSON mapping — pass an
    empty mapping explicitly when there is nothing to declare). ``permission``
    is the launch declaration's permission policy: the default
    ``BypassAll()`` skips permission checks; ``HomeSettings()`` puts no
    permission flag on the CLI so the home's ``settings.json`` permissions
    decide (agora-redesign #3753). Layer 2 must be answered outside it
    (``claude_process_layer_handler``, the fake, or an emulation's peer).
    """
    from doeff import run

    return run(
        _hy_headless_compose_module().claude_adapter(
            config_dir, env, settings, cold_resume_prompt, permission
        )
    )


_mock_effect_handler = MockAgentHandler()


def _hy_effectful_module():
    import hy  # noqa: F401, F811  # activate Hy import hook (intentionally re-imported per call site)

    return import_module("doeff_agents.handlers.effectful")


def agent_effectful_handler(
    *,
    session_repository: AgentSessionRepository | None = None,
    claude_runtime_policy: ClaudeRuntimePolicy | None = None,
    codex_runtime_policy: CodexRuntimePolicy | None = None,
) -> Any:
    """Return the real tmux handler as a Hy defhandler.

    The session backend is resolved through Ask(SessionBackend), so
    deployment-specific terminal paths are injected by the doeff environment.
    Claude runtime authentication/home policy stays owned by doeff-agents, but
    callers can pin it here without constructing TmuxAgentHandler directly.
    """
    return _hy_effectful_module().tmux_agent_defhandler(
        session_repository=session_repository,
        claude_runtime_policy=claude_runtime_policy,
            codex_runtime_policy=codex_runtime_policy,
    )


def default_agent_handler(
    *,
    backend: SessionBackend,
    session_repository: AgentSessionRepository | None = None,
    claude_runtime_policy: ClaudeRuntimePolicy | None = None,
    codex_runtime_policy: CodexRuntimePolicy | None = None,
) -> AgentHandler:
    """Return the default production AgentHandler without exposing transport class names.

    Most callers should install ``agent_effectful_handler()`` and only emit agent
    effects. MCP tools require the doeff-native handler path so their calls run
    inside the caller's doeff VM.
    """
    return TmuxAgentHandler(
        backend=backend,
        session_repository=session_repository,
        claude_runtime_policy=claude_runtime_policy,
            codex_runtime_policy=codex_runtime_policy,
    )


def mock_agent_handler() -> Any:
    """Return the mock testing handler as a Hy defhandler."""
    return _hy_effectful_module().agent_handler_defhandler(_mock_effect_handler)


def agent_effectful_handlers(
    *,
    time_handler: Any | None = None,
    session_repository: AgentSessionRepository | None = None,
    claude_runtime_policy: ClaudeRuntimePolicy | None = None,
    codex_runtime_policy: CodexRuntimePolicy | None = None,
) -> tuple[Any, ...]:
    """Return standard production handlers for real tmux agent workflows.

    High-level agent programs use ``doeff_time.Delay`` between monitor polls.
    Include a time handler by default so callers that use this convenience tuple
    do not accidentally leave Delay unhandled.
    """
    return (
        time_handler or sync_time_handler(),
        agent_effectful_handler(
            session_repository=session_repository,
            claude_runtime_policy=claude_runtime_policy,
            codex_runtime_policy=codex_runtime_policy,
        ),
    )


def daemon_agent_handler(
    *,
    socket_path: str | Path | None = None,
    db_path: str | Path | None = None,
    daemon_bin: str | Path | None = None,
    client: AgentdSessionClient | None = None,
    claude_runtime_policy: ClaudeRuntimePolicy | None = None,
    codex_runtime_policy: CodexRuntimePolicy | None = None,
    max_running: int = 10,
) -> Any:
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
            codex_runtime_policy=codex_runtime_policy,
    )
    return _hy_effectful_module().agent_handler_defhandler(agent_handler)


def daemon_agent_handlers(
    *,
    socket_path: str | Path | None = None,
    db_path: str | Path | None = None,
    daemon_bin: str | Path | None = None,
    client: AgentdSessionClient | None = None,
    claude_runtime_policy: ClaudeRuntimePolicy | None = None,
    codex_runtime_policy: CodexRuntimePolicy | None = None,
    time_handler: Any | None = None,
    max_running: int = 10,
) -> tuple[Any, ...]:
    """Return standard handlers for doeff-agentd-backed workflows."""
    return (
        time_handler or sync_time_handler(),
        daemon_agent_handler(
            socket_path=socket_path,
            db_path=db_path,
            daemon_bin=daemon_bin,
            client=client,
            claude_runtime_policy=claude_runtime_policy,
            codex_runtime_policy=codex_runtime_policy,
            max_running=max_running,
        ),
    )


def mock_agent_handlers(
    *,
    time_handler: Any | None = None,
) -> tuple[Any, ...]:
    """Return standard mock handlers, including a no-op Delay handler."""
    noop_time_handler = sync_time_handler(sleep=lambda _seconds: None)
    return (time_handler or noop_time_handler, mock_agent_handler())


def production_handlers(
    *,
    session_repository: AgentSessionRepository | None = None,
) -> tuple[Any, ...]:
    """Canonical handler tuple for production (tmux-backed) execution."""
    return agent_effectful_handlers(session_repository=session_repository)


def mock_handlers() -> tuple[Any, ...]:
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
