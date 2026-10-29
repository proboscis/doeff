"""Agent effect handler backed by the doeff-agentd supervisor daemon."""

import os
import shlex
from collections.abc import Generator, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Protocol

import hy  # noqa: F401  # activate the Hy import hook for the shared handler programs (effectful.hy)

from doeff import Effect, K, Pass, Resume, handler
from doeff.program import ProgramHandler
from doeff_agents._adapters.base import AgentType, LaunchParams
from doeff_agents._agentd_client import RPC_ERR_NO_SUCH_SESSION, AgentdClientError
from doeff_agents._claude_home import prepare_claude_home
from doeff_agents._effects import AgentCapabilityUnsupportedError as _AgentCapabilityUnsupportedError
from doeff_agents._effects import (
    AgentEffect,
    AgentError,
    AgentLaunchError,
    AgentNotAvailableError,
    AgentSessionLifecycle,
    AgentSessionSnapshot,
    AttachAgentSessionEffect,
    AwaitOutcome,
    AwaitResultEffect,
    CancelAgentSessionEffect,
    CaptureEffect,
    CleanupAgentSessionEffect,
    FollowUpEffect,
    GetAgentSessionEffect,
    LaunchEffect,
    LaunchSessionEffect,
    ListAgentSessionsEffect,
    MonitorEffect,
    Observation,
    ObserveAgentSessionEffect,
    ReleaseSessionEffect,
    SendEffect,
    SessionHandle,
    SessionNotFoundError,
    StopEffect,
    StopSessionEffect,
    WarmSessionEffect,
)
from doeff_agents._handlers.effectful import _await_result_by_polling
from doeff_agents._handlers.production import _run_agent_task, get_adapter
from doeff_agents._effects._handler_effects import ClaudeLaunchEffect, refuse_turn_capabilities
from doeff_agents._io_root import IoRoot
from doeff_agents._runtime import ClaudeRuntimePolicy, CodexRuntimePolicy
from doeff_agents._shell import (
    assert_no_forbidden_agent_env,
    assert_session_env_is_non_auth_overlay,
    wrap_with_shell_exports,
)
from doeff_agents._tmux import attach_session as _attach_tmux_session

_HANDLER_NAME = "daemon_agent_handler"
_NO_MCP_LIFECYCLE = "doeff-agentd does not manage MCP lifecycle; prepare MCP with defmcp"


class AgentdSessionClient(Protocol):
    """Subset of AgentdClient the daemon agent handler uses."""

    def launch_session(
        self,
        *,
        session_id: str,
        session_name: str,
        agent_type: str,
        work_dir: Path,
        command: str | None = None,
        prompt: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        lifecycle: AgentSessionLifecycle,
        binding: Mapping[str, object] | None = None,
        session_env: Mapping[str, str] | None = None,
        expected_result: Mapping[str, object] | None = None,
    ) -> AgentSessionSnapshot: ...

    def await_result(
        self,
        session_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AwaitOutcome: ...

    def get_session(self, session_id: str) -> AgentSessionSnapshot | None: ...

    def list_sessions(
        self,
        query=None,
    ) -> tuple[AgentSessionSnapshot, ...]: ...

    def capture_session(self, session_id: str, *, lines: int = 100) -> str: ...

    def send_session(
        self,
        session_id: str,
        message: str,
        *,
        enter: bool = True,
        literal: bool = True,
    ) -> None: ...

    def cancel_session(self, session_id: str) -> AgentSessionSnapshot: ...

    def cleanup_session(self, session_id: str) -> AgentSessionSnapshot: ...


@dataclass(frozen=True)
class _Daemon:
    """What the daemon agent handler launches and asks with: the agentd client and the runtime policies.

    ``io`` performs the driver layer's I/O (CLI availability, the Claude home) — production or test (段 7 lane 7c).
    """

    client: AgentdSessionClient
    io: IoRoot
    claude_runtime_policy: ClaudeRuntimePolicy
    codex_runtime_policy: CodexRuntimePolicy


def _daemon_agent_handler(
    *,
    client: AgentdSessionClient,
    claude_runtime_policy: ClaudeRuntimePolicy | None = None,
    codex_runtime_policy: CodexRuntimePolicy | None = None,
    io_root: IoRoot | None = None,
) -> ProgramHandler:
    """Build the doeff handler that delegates agent session lifecycle to doeff-agentd through ``client``.

    It answers the agent effect, the schema-result (L2) effects and the terminal-session effects; agentd owns
    the session and its result contract. MCP tools are refused (agentd does not manage MCP lifecycle).
    """
    daemon = _Daemon(
        client=client,
        io=io_root if io_root is not None else _default_io_root(),
        claude_runtime_policy=claude_runtime_policy or ClaudeRuntimePolicy(),
        codex_runtime_policy=codex_runtime_policy or CodexRuntimePolicy(),
    )

    @handler
    def answer_on_agentd(effect: Effect, k: K) -> Generator[Any, Any, Any]:
        """Answer an agent effect through doeff-agentd; pass every other effect outward."""
        # The effects it answers, by group; AwaitResult — the one that waits on the scheduler — apart.
        match effect:
            case AwaitResultEffect():
                value = yield _await_result_by_polling(
                    partial(_await_once, daemon), effect.handle, effect.timeout_seconds
                )
            case (
                AgentEffect()
                | LaunchSessionEffect()
                | FollowUpEffect()
                | StopSessionEffect()
                | ReleaseSessionEffect()
                | WarmSessionEffect()
            ):
                value = _answer_schema_result(daemon, effect)
            case LaunchEffect() | ClaudeLaunchEffect() | MonitorEffect() | CaptureEffect() | SendEffect() | StopEffect():
                value = _answer_terminal(daemon, effect)
            case (
                GetAgentSessionEffect()
                | ListAgentSessionsEffect()
                | ObserveAgentSessionEffect()
                | AttachAgentSessionEffect()
                | CancelAgentSessionEffect()
                | CleanupAgentSessionEffect()
            ):
                value = _answer_session_id(daemon, effect)
            case _:
                return (yield Pass(effect, k))
        return (yield Resume(k, value))

    return answer_on_agentd


def _answer_schema_result(daemon: _Daemon, effect: Effect) -> object:
    """Answer the agent effect and the schema-result session effects (all but AwaitResult) on agentd."""
    match effect:
        case AgentEffect():
            return _run_agent_task(
                effect.task,
                launch_session=partial(_launch_session, daemon),
                await_result=partial(_await_once, daemon),
                follow_up=partial(_follow_up, daemon),
                release_session=_release_session,
            )
        case LaunchSessionEffect():
            return _launch_session(daemon, effect)
        case FollowUpEffect():
            return _follow_up(daemon, effect)
        case StopSessionEffect():
            daemon.client.cancel_session(effect.handle.session_id)
            return None
        case ReleaseSessionEffect():
            return _release_session(effect)
        case WarmSessionEffect():
            # 入力の前に runtime を事前起動する WarmSessionEffect は、agentd の会話では LaunchEffect が既に CLI を起動して
            # 入力を待たせている — 黙って何もせずに応答せず、型で拒否する。
            raise _AgentCapabilityUnsupportedError(capability="WarmSessionEffect", handler=_HANDLER_NAME)
        case _:
            raise _not_in_group(effect)


def _answer_terminal(daemon: _Daemon, effect: Effect) -> object:
    """Answer the terminal-session effects (launch, watch, type into, stop) on agentd."""
    match effect:
        case LaunchEffect():
            return _launch_terminal(daemon, effect)
        case ClaudeLaunchEffect():
            return _launch_claude_terminal(daemon, effect)
        case MonitorEffect():
            return _monitor(daemon, effect)
        case CaptureEffect():
            return daemon.client.capture_session(effect.handle.session_id, lines=effect.lines)
        case SendEffect():
            daemon.client.send_session(
                effect.handle.session_id,
                effect.message,
                enter=effect.enter,
                literal=effect.literal,
            )
            return None
        case StopEffect():
            daemon.client.cancel_session(effect.handle.session_id)
            return None
        case _:
            raise _not_in_group(effect)


def _answer_session_id(daemon: _Daemon, effect: Effect) -> object:
    """Answer the effects that name a session by id (read, observe, attach, cancel, clean up) on agentd."""
    match effect:
        case GetAgentSessionEffect():
            return daemon.client.get_session(effect.session_id)
        case ListAgentSessionsEffect():
            return daemon.client.list_sessions(effect.query)
        case ObserveAgentSessionEffect():
            return _require_snapshot(daemon, effect.session_id)
        case AttachAgentSessionEffect():
            return _attach_session(daemon, effect)
        case CancelAgentSessionEffect():
            return daemon.client.cancel_session(effect.session_id)
        case CleanupAgentSessionEffect():
            return daemon.client.cleanup_session(effect.session_id)
        case _:
            raise _not_in_group(effect)


def _not_in_group(effect: Effect) -> TypeError:
    """The handler routed an effect to a group that does not hold it — a bug in the routing, not in the program."""
    return TypeError(f"daemon agent handler: no answer for {type(effect).__name__} in its group")


def _launch_terminal(daemon: _Daemon, effect: LaunchEffect) -> SessionHandle:
    """Answer Launch: build the launch command and register the session with doeff-agentd."""
    refuse_turn_capabilities(effect, handler=_HANDLER_NAME)
    if effect.mcp_tools:
        raise AgentLaunchError(_NO_MCP_LIFECYCLE)
    return _launch(
        daemon,
        session_name=effect.session_name,
        agent_type=effect.agent_type,
        work_dir=Path(effect.work_dir),
        prompt=effect.prompt,
        model=effect.model,
        effort=effect.effort,
        bare=effect.bare,
        lifecycle=effect.lifecycle,
        session_env=effect.session_env,
    )


def _launch_claude_terminal(daemon: _Daemon, effect: ClaudeLaunchEffect) -> SessionHandle:
    """Answer the Claude-specific launch: a Claude session through doeff-agentd."""
    if effect.mcp_tools:
        raise AgentLaunchError(_NO_MCP_LIFECYCLE)
    return _launch(
        daemon,
        session_name=effect.session_name,
        agent_type=AgentType.CLAUDE,
        work_dir=effect.work_dir,
        prompt=effect.prompt,
        model=effect.model,
        effort=effect.effort,
        bare=effect.bare,
        lifecycle=effect.lifecycle,
        session_env=effect.session_env,
    )


def _monitor(daemon: _Daemon, effect: MonitorEffect) -> Observation:
    """Answer Monitor with the daemon's latest recorded session state."""
    snapshot = _require_snapshot(daemon, effect.handle.session_id)
    return Observation(
        status=snapshot.status,
        output_changed=False,
        output_snippet=snapshot.output_snippet,
    )


def _attach_session(daemon: _Daemon, effect: AttachAgentSessionEffect) -> None:
    """Put this terminal on a session the host carries, so an operator can watch or type into it.

    Only a tmux session has a terminal to join; the host names its tmux
    session, and the attach (or a client switch when already inside tmux)
    runs on this machine until the operator detaches.
    """
    snapshot = _require_snapshot(daemon, effect.session_id)
    if snapshot.backend_kind != "tmux":
        raise AgentError(
            f"Session '{snapshot.session_id}' is not backed by tmux "
            f"(backend: {snapshot.backend_kind})"
        )
    _attach_tmux_session(snapshot.session_name)


def _launch(
    daemon: _Daemon,
    *,
    session_name: str,
    agent_type: AgentType,
    work_dir: Path,
    prompt: str | None,
    model: str | None,
    effort: str | None,
    bare: bool,
    lifecycle: AgentSessionLifecycle,
    session_env: Mapping[str, str] | None,
) -> SessionHandle:
    """Register a terminal launch with doeff-agentd: the CLI must be available and the env non-auth."""
    adapter = get_adapter(agent_type)
    if not daemon.io(adapter.available()):
        raise AgentNotAvailableError(f"{agent_type.value} CLI is not available")

    assert_no_forbidden_agent_env(
        session_env,
        context="AgentdAgentHandler session_env",
    )
    # ADR-DOE-AGENTS-004 R9: the wire launch is auth-blind — session_env
    # is a non-auth overlay (the host rejects binding-owned keys in it)
    # and codex auth travels as the typed `binding` field, derived from
    # the handler binder below.
    assert_session_env_is_non_auth_overlay(
        session_env,
        context="AgentdAgentHandler session_env",
    )
    binding = _wire_binding(daemon, agent_type)
    tmux_env: dict[str, str] = dict(session_env or {})
    command_env: dict[str, str] = dict(session_env or {})
    _prepare_agent_environment(daemon, agent_type, work_dir, tmux_env, command_env)

    command: str | None = None
    if agent_type not in (AgentType.CLAUDE, AgentType.CODEX):
        argv = adapter.launch_command(
            LaunchParams(
                work_dir=work_dir,
                prompt=None,
                model=model,
                effort=effort,
                bare=bare,
            )
        )
        command = wrap_with_shell_exports(shlex.join(argv), command_env)
    snapshot = daemon.client.launch_session(
        session_id=session_name,
        session_name=session_name,
        agent_type=agent_type.value,
        work_dir=work_dir,
        command=command,
        prompt=prompt,
        model=model,
        effort=effort,
        lifecycle=lifecycle,
        binding=binding,
        session_env=tmux_env,
    )
    return snapshot.to_handle()


def _launch_session(daemon: _Daemon, effect: LaunchSessionEffect) -> SessionHandle:
    """Answer LaunchSession: idempotently launch or re-adopt an agentd session."""
    if effect.spec.mcp_tools:
        raise AgentLaunchError(_NO_MCP_LIFECYCLE)
    session_id = effect.spec.session_id
    existing = daemon.client.get_session(session_id)
    if existing is not None:
        return SessionHandle(session_id=session_id)

    adapter = get_adapter(effect.spec.agent_type)
    if not daemon.io(adapter.available()):
        raise AgentNotAvailableError(f"{effect.spec.agent_type.value} CLI is not available")

    assert_no_forbidden_agent_env(
        effect.spec.session_env,
        context="AgentSpec.session_env",
    )
    assert_session_env_is_non_auth_overlay(
        effect.spec.session_env,
        context="AgentSpec.session_env",
    )
    binding = _wire_binding(daemon, effect.spec.agent_type)
    tmux_env: dict[str, str] = dict(effect.spec.session_env or {})
    command_env: dict[str, str] = dict(effect.spec.session_env or {})
    _prepare_agent_environment(daemon, effect.spec.agent_type, effect.spec.work_dir, tmux_env, command_env)

    # agentd owns the concrete result-file contract.  The Python layer
    # supplies only the schema and retry budget.
    daemon.client.launch_session(
        session_id=session_id,
        session_name=session_id,
        agent_type=effect.spec.agent_type.value,
        work_dir=effect.spec.work_dir,
        prompt=effect.spec.prompt,
        model=effect.spec.model,
        effort=effect.spec.effort,
        lifecycle=effect.spec.lifecycle,
        binding=binding,
        session_env=tmux_env,
        expected_result={
            "payload_schema": effect.spec.result_schema,
            "max_retries": effect.spec.max_retries,
        },
    )
    return SessionHandle(session_id=session_id)


def _await_once(daemon: _Daemon, effect: AwaitResultEffect) -> AwaitOutcome:
    """Answer one AwaitResult round-trip with agentd's single await_result handoff."""
    try:
        return daemon.client.await_result(
            effect.handle.session_id,
            timeout_seconds=effect.timeout_seconds,
        )
    except AgentdClientError as exc:
        if exc.error_code == RPC_ERR_NO_SUCH_SESSION:
            raise SessionNotFoundError(effect.handle.session_id) from exc
        raise


def _follow_up(daemon: _Daemon, effect: FollowUpEffect) -> SessionHandle:
    """Answer FollowUp: continue a daemon-owned session."""
    refuse_turn_capabilities(effect, handler=_HANDLER_NAME)
    daemon.client.send_session(effect.handle.session_id, effect.message)
    return effect.handle


def _release_session(effect: ReleaseSessionEffect) -> None:
    """Answer ReleaseSession: a no-op for daemon-owned durable sessions."""
    return


def _prepare_agent_environment(
    daemon: _Daemon,
    agent_type: AgentType,
    work_dir: Path,
    tmux_env: dict[str, str],
    command_env: dict[str, str],
) -> None:
    """Give a Claude session its own home (trusted workspaces, no auto update) before the launch."""
    if agent_type == AgentType.CLAUDE:
        policy = daemon.claude_runtime_policy
        assert_no_forbidden_agent_env(
            policy.bootstrap_exports,
            context="ClaudeRuntimePolicy.bootstrap_exports",
        )
        agent_home = policy.agent_home or work_dir / ".agent-home"
        trusted_workspaces = policy.trusted_workspaces or (work_dir,)
        daemon.io(prepare_claude_home(agent_home, trusted_workspaces))
        tmux_env.setdefault("DISABLE_AUTO_UPDATE", "true")
        tmux_env.setdefault("DISABLE_UPDATE_PROMPT", "true")
        claude_env = {
            "HOME": str(agent_home),
            "CLAUDE_HOME": str(agent_home / ".claude"),
            **policy.bootstrap_exports,
        }
        tmux_env.update(claude_env)
        command_env.update(claude_env)
    # NOTE (R9, 2026-07-08): the codex branch is gone.  The session host
    # owns codex pre-launch physics (trust setup) and derives it from
    # the typed `binding` we now send — a second local trust writer was
    # exactly the two-writers drift R9 retired.


def _wire_binding(daemon: _Daemon, agent_type: AgentType) -> dict[str, object] | None:
    """Typed wire binding derived from the handler binder (R9).

    CODEX_HOME comes from the handler's policy first, then the binder
    process environment (the process that bound this handler configured
    its own env).  When neither names a home, send no binding and let
    the host's explicit-auth gate reject the launch — the gate has one
    authority and it is not this client.
    """
    if agent_type != AgentType.CODEX:
        return None
    policy_home = daemon.codex_runtime_policy.codex_home
    codex_home = (
        str(policy_home) if policy_home is not None else os.environ.get("CODEX_HOME")
    )
    if not codex_home:
        return None
    return {"kind": "codex", "codex_home": codex_home}


def _require_snapshot(daemon: _Daemon, session_id: str) -> AgentSessionSnapshot:
    """The daemon's snapshot of a session it registered; an unknown id is an error."""
    snapshot = daemon.client.get_session(session_id)
    if snapshot is None:
        raise SessionNotFoundError(f"Session {session_id} is not registered")
    return snapshot


def _default_io_root() -> IoRoot:
    """The production driver I/O (the handler's default when no test passes its own)."""
    from doeff_agents._io_handlers import run_driver_io

    return run_driver_io
