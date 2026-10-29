"""Effect handlers for agent session management.

The public surface is the handler factories (each returns doeff handlers to
install around a program that yields ``doeff_agents._effects``) and the values
those factories take. The handler implementation classes live in the inner
modules and are not part of the public API.
"""


from collections.abc import Callable, Mapping, Sequence
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

import hy  # noqa: F401  # activate Hy import hook for handler modules
from doeff_claude_code import BypassAll, PermissionPolicy
from doeff_time import sync_time_handler

from doeff.program import ProgramHandler

if TYPE_CHECKING:
    from doeff_claude_code import ClaudeLiveLimitExceeded as ClaudeLiveLimitExceeded
    from doeff_claude_code.fake import FakeClaudeWorld as FakeClaudeWorld
    from doeff_claude_code.fake import FakeReply as FakeReply
    from doeff_codex import ApprovalPolicy as CodexApprovalPolicy
    from doeff_codex import FakeCodexWorld as FakeCodexWorld
    from doeff_codex import SandboxMode as CodexSandboxMode

from doeff_agents._agentd_client import LazyAgentdClient as _LazyAgentdClient
from doeff_agents._handlers.daemon import AgentdSessionClient as AgentdSessionClient
# The handler objects behind the factories (inner modules — not re-exported).
from doeff_agents._handlers.daemon import DaemonAgentHandler as _DaemonAgentHandler
from doeff_agents._handlers.testing import MockAgentHandler as _MockAgentHandler
from doeff_agents._handlers.testing import ScenarioAgentHandler as _ScenarioAgentHandler
from doeff_agents._handlers.testing import MockSessionScript as MockSessionScript
from doeff_agents._handlers.testing import ScenarioStep as ScenarioStep
from doeff_agents._runtime import ClaudeRuntimePolicy as ClaudeRuntimePolicy
from doeff_agents._runtime import CodexRuntimePolicy as CodexRuntimePolicy
from doeff_agents.session_backend import SessionBackend
from doeff_agents.session_store import AgentSessionRepository as AgentSessionRepository

# The headless adapter's default permission policy: skip the CLI's permission
# checks (`BypassAll`) — the behavior every caller had before #3753.
_DEFAULT_CLAUDE_PERMISSION: PermissionPolicy = BypassAll()

# Values the Claude and codex runtime handlers take or raise, defined in
# doeff-claude-code's and doeff-codex's Hy modules (name → defining module and
# its name there): read on first use so importing this module does not load them.
_RUNTIME_VALUES = {
    "ClaudeLiveLimitExceeded": ("doeff_claude_code._effects", "ClaudeLiveLimitExceeded"),
    "FakeClaudeWorld": ("doeff_claude_code.fake", "FakeClaudeWorld"),
    "FakeReply": ("doeff_claude_code.fake", "FakeReply"),
    "FakeCodexWorld": ("doeff_codex", "FakeCodexWorld"),
    "CodexApprovalPolicy": ("doeff_codex", "ApprovalPolicy"),
    "CodexSandboxMode": ("doeff_codex", "SandboxMode"),
}


def __getattr__(name: str) -> Any:
    source = _RUNTIME_VALUES.get(name)
    if source is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = source
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


# ---------------------------------------------------------------------------
# New handler composition — claude_resolver + claude_handler (Hy-based)
# ---------------------------------------------------------------------------

def claude_agent_handler(*, backend=None) -> ProgramHandler:
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

    claude_handler = import_module("doeff_agents._handlers.claude").claude_handler
    return claude_handler(backend=backend)


def codex_agent_handler(*, backend=None) -> ProgramHandler:
    """Codex agent handler (Hy-based architecture)."""
    import hy  # noqa: F401, F811  # activate Hy import hook (intentionally re-imported per call site)

    codex_handler = import_module("doeff_agents._handlers.codex").codex_handler
    return codex_handler(backend=backend)


def _hy_headless_compose_module() -> Any:
    import hy  # noqa: F401, F811  # activate Hy import hook (intentionally re-imported per call site)

    return import_module("doeff_agents._handlers.headless_compose")


def claude_agent_runtime_handlers(
    *,
    config_dir: str,
    env: dict[str, str],
    live_limit: int | None,
    credential_floor_seconds: float,
    settings: dict[str, Any] | None = None,
    cold_resume_prompt: str | None = None,
    command: tuple[str, ...] = ("claude",),
) -> list[ProgramHandler]:
    """The agent runtime for Claude, as doeff-agents chooses it (agora-redesign #606).

    Callers that must not know the substrate (agora: "the agent runtime's
    substrate is the library's concern", operator decision O5) ask for the
    Claude agent runtime by this name; which substrate answers the public
    effects is decided here. Today it is the print-mode (headless) adapter
    over ``doeff-claude-code``: returns ``[doeff-claude-code production
    handler, headless adapter]`` in ``with_handlers`` order (outer first).
    ``config_dir`` / ``env`` are the Claude home (credentials are placed in
    ``env`` by the composition root). ``live_limit`` is how many CLI processes
    the host keeps alive at once and ``credential_floor_seconds`` how long
    before a lent credential expires the host stops the process using it
    (both from the caller's declaration — no defaults; agora-redesign #3672
    D2). ``live_limit=None`` declares no limit: for a caller that decides how
    many to keep alive by another measure (the machine's free memory), layer 2
    then compares nothing and emits neither the over-limit log line nor
    ``ClaudeLiveLimitExceeded`` (agora-redesign #4282; ``None`` is passed
    through as is, and it is not a default — such a caller passes it
    explicitly). ``command`` is the CLI executable and its leading arguments.
    Install a doeff-time handler, a slog handler (the production handler
    emits CLI launch timing lines — agora-redesign #3605), a handler that
    answers ``ClaudeLiveLimitExceeded`` (the host: layer 2 never stops a CLI
    for the live limit — a launch past it is reported to the host instead,
    agora-redesign #4072 E1b; the type is ``doeff_agents._handlers.ClaudeLiveLimitExceeded``)
    and the scheduler outside them. No session-host socket is opened
    (agora-redesign #604).
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


def fake_claude_agent_runtime_handlers(
    *,
    responder: Any = None,
    config_dir: str = "fake-claude-home",
    world: Any = None,
    env: Mapping[str, str],
    settings: Mapping[str, object],
    permission: PermissionPolicy = _DEFAULT_CLAUDE_PERMISSION,
) -> list[ProgramHandler]:
    """The fake counterpart of ``claude_agent_runtime_handlers`` (no process, no API).

    The same adapter over doeff-claude-code's fake handler.
    ``responder(text, memory) -> FakeReply`` scripts each turn
    (``doeff_agents._handlers.FakeReply``). Pass ``world`` (a
    ``FakeClaudeWorld`` the caller keeps — e.g. ``world.restarted()`` for a
    new process over the same home) instead of ``responder``; exactly one.
    ``env`` / ``settings`` are the same home env (str → str) and CLI settings
    (a JSON mapping) the production runtime takes; both are required
    (agora-redesign #3387): a caller's emulation passes what its production
    path decided, so the launch declaration the fake layer 2 receives carries
    them (agora-redesign #3327); a caller with nothing to declare passes empty
    mappings explicitly. ``permission`` is the launch declaration's permission
    policy, as for ``claude_agent_adapter_handler`` (default ``BypassAll()`` —
    agora-redesign #3753).
    Returns ``[fake layer-2 handler, headless adapter]`` (outer first).
    """
    return _hy_headless_compose_module().fake_headless_claude_handlers(
        responder, config_dir, world, env=env, settings=settings, permission=permission
    )


def claude_process_layer_handler(
    *,
    live_limit: int | None,
    credential_floor_seconds: float,
    command: tuple[str, ...] = ("claude",),
) -> ProgramHandler:
    """Layer 2 alone: the production handler that keeps each conversation's CLI process alive across turns.

    The same handler ``claude_agent_runtime_handlers`` returns first. For a
    caller that places layer 2 and the adapter at different depths
    (agora-redesign #3507): layer 2 belongs to the foundation that owns real
    processes (an emulation answers layer 2 outside instead), the adapter sits
    next to the program. ``live_limit`` / ``credential_floor_seconds`` as for
    ``claude_agent_runtime_handlers`` (agora-redesign #3672 D2). Install a
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
) -> ProgramHandler:
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
) -> ProgramHandler:
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


def codex_process_layer_handler(
    *,
    command: tuple[str, ...] = ("codex",),
    launch_timeout: float = 120.0,
) -> ProgramHandler:
    """Codex layer 2 alone: the production handler that keeps each conversation's app-server process alive across turns.

    The codex counterpart of ``claude_process_layer_handler`` (card
    acp:kanban-issue:ki-f63f65c153b0): a caller that places layer 2 and the
    adapter at different depths builds both from these public entries — the
    composition module's name carries the lower-layer word, which agora's
    boundary rules keep out of agora's code. ``command`` is the executable
    and its leading arguments (resolved through the PATH of the adapter's
    env); ``launch_timeout`` bounds the start and each request's answer
    (seconds). Install a doeff-time handler and the scheduler outside it.
    """
    from doeff import run

    return run(_hy_headless_compose_module().codex_process_layer(tuple(command), float(launch_timeout)))


def fake_codex_process_layer_handler(*, world: "FakeCodexWorld") -> ProgramHandler:
    """Codex layer 2 alone, fake (no process, no API).

    ``world`` holds the scripted replies (``CodexTurnInput`` → ``FakeCodexReply``)
    and the fake's state.
    """
    from doeff import run

    return run(_hy_headless_compose_module().fake_codex_process_layer(world))


def codex_agent_adapter_handler(
    *,
    env: Mapping[str, str],
    approval_policy: "CodexApprovalPolicy | None" = None,
    sandbox: "CodexSandboxMode | None" = None,
    disabled_features: tuple[str, ...] = (),
) -> ProgramHandler:
    """The codex adapter alone: the public effects onto codex layer-2 effects.

    The same adapter whether layer 2 is the production handler or the fake.
    ``env`` is the codex home env (PATH, HOME, CODEX_HOME — the borrowed
    credential is placed per process by layer 2). ``approval_policy`` /
    ``sandbox`` default to the adapter's declaration defaults (never ask;
    full access — the turn already runs in the caller's sandbox).
    ``disabled_features`` names the codex features each process starts with
    turned off (``--disable <name>`` — e.g. ``("hooks",)`` runs no hook from
    any layer); empty keeps codex's defaults. Layer 2 must be answered
    outside it (``codex_process_layer_handler`` or the fake).
    """
    from doeff import run

    compose = _hy_headless_compose_module()
    policy = compose.CodexApprovalPolicy.NEVER if approval_policy is None else approval_policy
    mode = compose.CodexSandboxMode.DANGER_FULL_ACCESS if sandbox is None else sandbox
    return run(compose.codex_adapter(dict(env), policy, mode, tuple(disabled_features)))


def _hy_effectful_module():
    import hy  # noqa: F401, F811  # activate Hy import hook (intentionally re-imported per call site)

    return import_module("doeff_agents._handlers.effectful")


def agent_effectful_handler(
    *,
    session_repository: AgentSessionRepository | None = None,
    claude_runtime_policy: ClaudeRuntimePolicy | None = None,
    codex_runtime_policy: CodexRuntimePolicy | None = None,
) -> ProgramHandler:
    """Return the real tmux handler as a Hy defhandler.

    The session backend is resolved through Ask(SessionBackend), so
    deployment-specific terminal paths are injected by the doeff environment.
    Claude runtime authentication/home policy stays owned by doeff-agents, but
    callers can pin it here without constructing the handler object themselves.
    """
    return _hy_effectful_module().tmux_agent_defhandler(
        session_repository=session_repository,
        claude_runtime_policy=claude_runtime_policy,
        codex_runtime_policy=codex_runtime_policy,
    )


def mock_agent_handler(*, sessions: Sequence[MockSessionScript] = ()) -> ProgramHandler:
    """Return a fresh mock testing handler (a Hy defhandler) that answers each scripted session.

    ``sessions`` are ``MockSessionScript`` values, each naming its session
    (``session_name``), the observations its ``Monitor`` effects return in turn
    and the screen text before the first one (``initial_output``). The handler
    keeps its own state — nothing is shared between two calls.
    """
    mock = _MockAgentHandler()
    for script in sessions:
        if script.session_name is None:
            raise ValueError("mock_agent_handler: every MockSessionScript in sessions names its session_name")
        mock.configure_session(script)
    return _hy_effectful_module().agent_handler_defhandler(mock)


def scenario_agent_handler(*, scripts: Mapping[str, Sequence[ScenarioStep]]) -> ProgramHandler:
    """Return a scenario-driven testing handler (a Hy defhandler) for the schema-result effects.

    ``scripts`` maps a deterministic session id (``AgentSpec.session_id``) to
    the ``ScenarioStep`` values its awaits return in turn, so retries are
    explicit in the test data. The agent is replaced by swapping this handler
    in; application functions are never mocked.
    """
    return _hy_effectful_module().agent_handler_defhandler(
        _ScenarioAgentHandler(scripts={session_id: tuple(steps) for session_id, steps in scripts.items()})
    )


def agent_effectful_handlers(
    *,
    time_handler: Any | None = None,
    session_repository: AgentSessionRepository | None = None,
    claude_runtime_policy: ClaudeRuntimePolicy | None = None,
    codex_runtime_policy: CodexRuntimePolicy | None = None,
) -> tuple[ProgramHandler, ...]:
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
) -> ProgramHandler:
    """Return the daemon-backed agent handler as a Hy defhandler."""
    active_client = client
    if active_client is None:
        active_client = _LazyAgentdClient(
            socket_path=socket_path,
            db_path=db_path,
            daemon_bin=daemon_bin,
            max_running=max_running,
        )
    agent_handler = _DaemonAgentHandler(
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
) -> tuple[ProgramHandler, ...]:
    """Return the reference handlers for doeff-agentd-backed workflows."""
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
    sessions: Sequence[MockSessionScript] = (),
) -> tuple[ProgramHandler, ...]:
    """Return standard mock handlers: a no-op Delay handler and a fresh ``mock_agent_handler(sessions=…)``."""
    noop_time_handler = sync_time_handler(sleep=lambda _seconds: None)
    return (time_handler or noop_time_handler, mock_agent_handler(sessions=sessions))

