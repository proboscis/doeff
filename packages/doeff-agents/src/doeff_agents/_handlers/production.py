"""The production tmux transport's answers to the agent effects.

tmux_agent_defhandler (effectful.hy) answers each agent effect with one function here, over the tmux
world ``_TmuxAgentState`` it builds on its first effect: the terminal backend, the runtime policies, the
session repository and the sessions it opened."""

import json
import os
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from doeff_agents import _tmux as tmux_transport
from doeff_agents._adapters.base import (
    AgentAdapter,
    AgentSessionLifecycle,
    AgentType,
    InjectionMethod,
    LaunchParams,
)
from doeff_agents._adapters.claude import ClaudeAdapter
from doeff_agents._adapters.codex import CodexAdapter, trust_workspace_in_codex_home
from doeff_agents._adapters.gemini import GeminiAdapter
from doeff_agents._agentd_client import DEFAULT_AWAIT_BUDGET_SECONDS
from doeff_agents._claude_home import prepare_claude_home
from doeff_agents._effects import (
    AgentAttemptExhaustedError,
    AgentDeadlineExceededError,
    AgentEffect,
    AgentLaunchError,
    AgentNotAvailableError,
    AgentReadyTimeoutError,
    AgentSessionSnapshot,
    AgentSpec,
    AgentTask,
    AgentValidationErrorKind,
    AgentValidationFailure,
    AttachAgentSessionEffect,
    AwaitOutcome,
    AwaitResultEffect,
    AwaitStatus,
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
    SessionAlreadyExistsError,
    SessionHandle,
    SessionNotFoundError,
    StopEffect,
    StopSessionEffect,
)
from doeff_agents._effects._handler_effects import ClaudeLaunchEffect, refuse_turn_capabilities
from doeff_agents._io_root import IoRoot
from doeff_agents._monitor import (
    MonitorState,
    SessionStatus,
    detect_status,
    evolve_status,
    hash_content,
    is_waiting_for_input,
)
from doeff_agents._result_validation import validate_result_payload
from doeff_agents._runtime import ClaudeRuntimePolicy, CodexRuntimePolicy
from doeff_agents._session import _dismiss_onboarding_dialogs, deliver_prompt_when_ready
from doeff_agents._session_backend import SessionBackend
from doeff_agents._session_store import AgentSessionRepository, InMemoryAgentSessionRepository
from doeff_agents._shell import (
    assert_no_forbidden_agent_env,
    assert_session_env_is_non_auth_overlay,
    wrap_with_shell_exports,
)

REPORT_RESULT_TOOL_NAME = "report_result"


def spec_uses_report_result_transport(spec: AgentSpec) -> bool:
    """True when a session owes a structured result over the in-VM data channel.

    ADR-DOE-AGENTS-005 R1: EVERY schema session launched through the in-process
    defhandlers carries the ``report_result`` MCP tool — the launch site starts
    an in-VM server even when the spec has no domain tools. Terminal bytes are
    never a result transport (the marker/scrape vocabulary was deleted by R3).
    """
    return spec.result_schema is not None


def make_report_result_tool(
    sink: dict[str, object],
    result_schema: dict[str, object],
):
    """Build the in-VM ``report_result`` MCP tool bound to ``sink``.

    The tool validates the payload against ``result_schema`` at report time
    (schema rejection is immediate and in-band, so the agent can correct and
    re-call within the same turn) and stores accepted payloads byte-faithfully
    in ``sink["payload"]`` for result-first reads by ``handle_await_result``.
    """
    from doeff import do
    from doeff.mcp import McpParamSchema, McpToolDef

    @do
    def _report_result_mcp_handler(result):
        validation_error = validate_result_payload(result, result_schema)
        if validation_error is not None:
            return {"status": "rejected", "validation_error": validation_error}
        sink["payload"] = result
        return {"status": "accepted"}

    return McpToolDef(
        name=REPORT_RESULT_TOOL_NAME,
        description=(
            "Report the final structured result of this session. Call exactly "
            "once when the task is complete; the payload is validated against "
            "the session result schema and replies status=accepted or "
            "status=rejected with a validation_error to correct."
        ),
        params=(
            McpParamSchema(
                name="result",
                type="object",
                description="The structured result object satisfying the session result schema.",
            ),
        ),
        handler=_report_result_mcp_handler,
    )


@dataclass
class SessionState:
    """Mutable state for a session (internal to handler)."""

    handle: SessionHandle
    adapter: AgentAdapter
    pane_id: str
    agent_type: AgentType
    work_dir: Path
    lifecycle: AgentSessionLifecycle
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    result_schema: dict[str, object] | None = None
    monitor_state: MonitorState = field(default_factory=MonitorState)
    dismissed_permission_prompt_hashes: set[str] = field(default_factory=set)
    status: SessionStatus = SessionStatus.BOOTING


_adapters: dict[AgentType, type[AgentAdapter]] = {
    AgentType.CLAUDE: ClaudeAdapter,  # type: ignore[dict-item]
    AgentType.CODEX: CodexAdapter,  # type: ignore[dict-item]
    AgentType.GEMINI: GeminiAdapter,  # type: ignore[dict-item]
}


def register_adapter(agent_type: AgentType, adapter_class: type[AgentAdapter]) -> None:
    """Register a custom adapter."""
    _adapters[agent_type] = adapter_class


def get_adapter(agent_type: AgentType) -> AgentAdapter:
    """Get the adapter for an agent type."""
    adapter_class = _adapters.get(agent_type)
    if adapter_class is None:
        raise ValueError(f"No adapter registered for: {agent_type}")
    return adapter_class()


def _claude_mcp_permission_prompt_visible(output: str) -> bool:
    """Return True when Claude is blocking on an MCP approval prompt."""
    lowered = output.lower()
    return (
        "tool use" in lowered
        and "(mcp)" in lowered
        and "do you want to proceed?" in lowered
        and "esc to cancel" in lowered
    ) or (
        "new mcp server found" in lowered
        and "use this mcp server" in lowered
        and "continue without using this mcp server" in lowered
        and "enter to confirm" in lowered
        and "esc to cancel" in lowered
    )


def _run_agent_task(
    task: AgentTask,
    *,
    launch_session: Callable[[LaunchSessionEffect], SessionHandle],
    await_result: Callable[[AwaitResultEffect], AwaitOutcome],
    follow_up: Callable[[FollowUpEffect], SessionHandle],
    release_session: Callable[[ReleaseSessionEffect], None],
) -> object:
    """Run Launch → AwaitResult → validate/retry → Release for ``agent``.

    The four functions answer one L2 effect each, synchronously — the way the
    handler answering ``agent`` answers that effect.

    Retry authority is SINGLE: result-contract retries belong to the
    session supervisor (agentd's ``expected_result.max_retries`` — it
    feeds the agent corrective prompts while the session is alive).  This
    loop never re-litigates them:

    - a TERMINAL failure (the await resolved on a failed/exited session)
      is already final — the supervisor exhausted its retries and cleaned
      the session, so a follow-up here lands on a dead pane.  Observed
      live four times: the crash ("tmux send-keys failed") replaced a
      clean exhaustion error with a raw transport exception.
    - AWAITING_INPUT is the one outcome where a follow-up is the designed
      continuation (local-handler sessions have no supervisor-side
      retries); ``task.max_retries`` bounds those nudges.

    Wall-clock authority is the NODE-SPEC DEADLINE alone (L-K4-3, k8s
    ``activeDeadlineSeconds`` semantics): a TIMED_OUT await is pure
    transport-heartbeat expiry — the session is alive and still working —
    so it is NEVER surfaced as a node failure and never burns a retry
    attempt.  The loop re-awaits transparently until a terminal outcome
    or, when ``task.deadline_seconds`` is declared, until the deadline is
    exceeded — then it raises ``AgentDeadlineExceededError`` for the
    orchestrator to park as a gate.  The deadline also bounds NEW work:
    a validation failure observed past the window parks as the deadline
    gate instead of dispatching a follow-up retry prompt or claiming
    attempt exhaustion.  Extension is a gate answer; there is no
    automatic extension policy here.
    """
    handle = launch_session(LaunchSessionEffect(spec=task))
    started_at = time.monotonic()
    attempts = 0
    try:
        while True:
            outcome = await_result(
                AwaitResultEffect(
                    handle=handle,
                    timeout_seconds=_await_heartbeat_seconds(task, started_at),
                )
            )
            last_error = _validation_failure_from_outcome(outcome, task.result_schema)
            if last_error is None:
                return outcome.result

            if last_error.kind == AgentValidationErrorKind.TIMED_OUT:
                # Transport heartbeat expiry: carries no semantic
                # decision. Re-await — never interrupt the session with a
                # retry prompt, never burn an attempt, never fail the
                # node. The only wall-clock bound is the node deadline.
                _raise_if_deadline_exceeded(task, handle, started_at)
                continue

            if not outcome.continuable:
                # The outcome came from a TERMINAL session: the
                # supervisor already exhausted the contract retries and
                # reaped the pane. A follow-up here lands on a dead
                # session — the failure is final.
                raise AgentAttemptExhaustedError(
                    session_id=handle.session_id,
                    attempts=attempts + 1,
                    last_error=last_error,
                )

            # L-K4-3: no new work past the deadline (k8s does not start
            # containers past activeDeadlineSeconds). A validation
            # failure observed after the window must park as the
            # deadline gate — checked BEFORE the attempts check so
            # exhaustion vs deadline attribution stays honest, and
            # before the follow-up so no retry prompt is commissioned
            # into an expired window.
            _raise_if_deadline_exceeded(task, handle, started_at)

            if attempts >= task.max_retries:
                raise AgentAttemptExhaustedError(
                    session_id=handle.session_id,
                    attempts=attempts + 1,
                    last_error=last_error,
                )

            handle = follow_up(
                FollowUpEffect(handle=handle, message=_retry_message(last_error))
            )
            attempts += 1
    finally:
        release_session(ReleaseSessionEffect(handle=handle))


def _deadline_remaining_seconds(task: AgentTask, started_at: float) -> float | None:
    """Seconds left in the node-spec deadline window; None when undeclared."""
    if task.deadline_seconds is None:
        return None
    return task.deadline_seconds - (time.monotonic() - started_at)


def _await_heartbeat_seconds(task: AgentTask, started_at: float) -> float:
    """Per-await transport budget: the keep-alive heartbeat, deadline-capped.

    The heartbeat bounds ONE transport round-trip and carries no node
    semantics (L-K4-3). When the node declares a deadline, the heartbeat
    is capped to the remaining window so the loop observes the deadline
    promptly instead of sleeping a full heartbeat past it.
    """
    remaining = _deadline_remaining_seconds(task, started_at)
    if remaining is None:
        return DEFAULT_AWAIT_BUDGET_SECONDS
    return min(DEFAULT_AWAIT_BUDGET_SECONDS, max(remaining, 0.0))


def _raise_if_deadline_exceeded(
    task: AgentTask,
    handle: SessionHandle,
    started_at: float,
) -> None:
    deadline_seconds = task.deadline_seconds
    if deadline_seconds is None:
        return
    elapsed_seconds = time.monotonic() - started_at
    if elapsed_seconds >= deadline_seconds:
        raise AgentDeadlineExceededError(
            session_id=handle.session_id,
            deadline_seconds=deadline_seconds,
            elapsed_seconds=elapsed_seconds,
        )


def _validation_failure_from_outcome(  # noqa: PLR0911 - baseline cleanup keeps existing control flow unchanged
    outcome: AwaitOutcome,
    schema: dict[str, object],
) -> AgentValidationFailure | None:
    if outcome.status == AwaitStatus.TIMED_OUT:
        return AgentValidationFailure(
            kind=AgentValidationErrorKind.TIMED_OUT,
            message=outcome.validation_error or "timed out awaiting structured result",
        )
    if outcome.status == AwaitStatus.AWAITING_INPUT:
        return AgentValidationFailure(
            kind=AgentValidationErrorKind.AWAITING_INPUT,
            message=outcome.validation_error or "agent is awaiting input",
        )
    if outcome.result is None:
        if outcome.validation_error:
            return AgentValidationFailure(
                kind=AgentValidationErrorKind.INVALID,
                message=outcome.validation_error,
            )
        return AgentValidationFailure(
            kind=AgentValidationErrorKind.ABSENT,
            message="structured result is absent",
        )

    if outcome.validation_error:
        return AgentValidationFailure(
            kind=AgentValidationErrorKind.INVALID,
            message=outcome.validation_error,
        )

    validation_error = validate_result_payload(outcome.result, schema)
    if validation_error is not None:
        return AgentValidationFailure(
            kind=AgentValidationErrorKind.INVALID,
            message=validation_error,
        )
    return None


def _retry_message(error: AgentValidationFailure) -> str:
    if error.kind == AgentValidationErrorKind.ABSENT:
        return (
            "No structured result was reported. Complete the task and report "
            f"the result by calling the `{REPORT_RESULT_TOOL_NAME}` MCP tool "
            "exactly once; it validates the payload against the result schema."
        )
    if error.kind == AgentValidationErrorKind.INVALID:
        return (
            f"The structured result was invalid: {error.message}. "
            f"Call the `{REPORT_RESULT_TOOL_NAME}` MCP tool again with a "
            "corrected payload; it validates against the result schema."
        )
    return f"Cannot continue automatically: {error.message}"


def _result_contract_prompt(
    result_schema: dict[str, object],
    report_result_server: str,
) -> str:
    # Typed data channel only (ADR-DOE-AGENTS-005 R1-R3): the terminal never
    # carries the result, so TUI line-wrapping cannot corrupt it.
    schema_json = json.dumps(result_schema, ensure_ascii=False, indent=2, sort_keys=True)
    return (
        "\n\n## Structured Result Contract (managed by doeff-agents)\n"
        "When the task is complete, report exactly one structured result by "
        f"calling the `{REPORT_RESULT_TOOL_NAME}` MCP tool on the "
        f"`{report_result_server}` MCP server, passing the result object as "
        "its `result` argument. Do not create JSON result files in the "
        "workspace and do not print the result to the terminal; the tool "
        "call is the only result transport. This is a doeff-agents "
        "transport detail; the domain task only decides the JSON payload.\n\n"
        "The result object must satisfy this schema:\n"
        "```json\n"
        f"{schema_json}\n"
        "```\n\n"
        "The tool validates the payload: on status=rejected, fix the "
        "payload per the returned validation_error and call the tool "
        "again. Do not ask the caller how to return the result."
    )


def _prompt_with_result_contract(
    prompt: str | None,
    result_schema: dict[str, object] | None,
    report_result_server: str | None,
) -> str:
    base = prompt or ""
    if result_schema is None:
        return base
    if report_result_server is None:
        raise AgentLaunchError(
            "a schema session has no report_result server: in-process schema "
            "sessions must be launched through the L2 LaunchSession defhandler "
            "path, which registers the result sink and serves the "
            f"`{REPORT_RESULT_TOOL_NAME}` tool (ADR-DOE-AGENTS-005 R1/R5)"
        )
    return f"{base}{_result_contract_prompt(result_schema, report_result_server)}"


def _launch_effect_from_spec(
    spec: AgentSpec,
    report_result_server: str | None = None,
) -> LaunchEffect:
    return LaunchEffect(
        session_name=spec.session_id,
        agent_type=spec.agent_type,
        work_dir=spec.work_dir,
        prompt=_prompt_with_result_contract(
            spec.prompt, spec.result_schema, report_result_server
        ),
        model=spec.model,
        effort=spec.effort,
        bare=spec.bare,
        lifecycle=spec.lifecycle,
        mcp_tools=spec.mcp_tools,
        mcp_server_name=spec.mcp_server_name,
        session_env=spec.session_env,
    )


@dataclass
class _TmuxAgentState:
    """The tmux handler's world: the terminal transport and runtime policies, and the sessions it opened.

    tmux_agent_defhandler builds one (``_tmux_agent_state``) on its first effect and answers every effect
    from it through the functions below, one per effect. ``sessions`` holds each live session's
    handler-private state; ``result_sinks`` the report_result sinks (session id → ``{"payload": ...}``) the
    in-VM MCP tool feeds — registering a sink before launch is the single signal that the session uses the
    typed result transport, and ``_await_result`` reads it result-first, ahead of any terminal capture;
    ``mcp_servers`` the in-VM MCP server each launch with tools carries.
    """

    backend: SessionBackend
    session_repository: AgentSessionRepository
    claude_runtime_policy: ClaudeRuntimePolicy
    codex_runtime_policy: CodexRuntimePolicy
    # 段 7 lane 7c: driver 層の I/O を果たす家の選択点(本番 / 検)。
    io: IoRoot
    sessions: dict[str, SessionState] = field(default_factory=dict)
    result_sinks: dict[str, dict[str, object]] = field(default_factory=dict)
    mcp_servers: dict[str, object] = field(default_factory=dict)


def _tmux_agent_state(
    *,
    backend: SessionBackend,
    session_repository: AgentSessionRepository | None = None,
    claude_runtime_policy: ClaudeRuntimePolicy | None = None,
    codex_runtime_policy: CodexRuntimePolicy | None = None,
    io_root: IoRoot | None = None,
) -> _TmuxAgentState:
    """A fresh tmux world over ``backend``; an omitted repository, policy or I/O root takes its default."""
    return _TmuxAgentState(
        backend=backend,
        session_repository=session_repository or InMemoryAgentSessionRepository(),
        claude_runtime_policy=claude_runtime_policy or ClaudeRuntimePolicy(),
        codex_runtime_policy=codex_runtime_policy or CodexRuntimePolicy(),
        io=io_root if io_root is not None else _default_io_root(),
    )


def _create_result_sink(tmux: _TmuxAgentState, session_id: str) -> dict[str, object]:
    """Register and return the report_result sink for ``session_id``."""
    sink: dict[str, object] = {"payload": None}
    tmux.result_sinks[session_id] = sink
    return sink


def _discard_result_sink(tmux: _TmuxAgentState, session_id: str) -> None:
    """Drop the report_result sink registered for ``session_id``."""
    tmux.result_sinks.pop(session_id, None)


def _run_agent(tmux: _TmuxAgentState, effect: AgentEffect) -> object:
    """Answer the schema-validated ``agent`` effect with the attempt loop over the tmux L2 answers."""
    return _run_agent_task(
        effect.task,
        launch_session=lambda launch: _launch_session(tmux, launch),
        await_result=lambda await_effect: _await_result(tmux, await_effect),
        follow_up=lambda follow_up: _follow_up(tmux, follow_up),
        release_session=lambda release: _release_session(tmux, release),
    )


def _launch(
    tmux: _TmuxAgentState,
    effect: LaunchEffect,
    mcp_servers: dict[str, str] | None = None,
) -> SessionHandle:
    """Launch a new agent session in tmux.

    If ``effect.mcp_tools`` is non-empty, the Hy doeff handler must already
    have started an in-VM MCP server and passed its URL in ``mcp_servers``.
    """
    refuse_turn_capabilities(effect, handler="tmux_agent_defhandler")
    adapter = get_adapter(effect.agent_type)
    # The effect carries a plain path value; the filesystem Path is built here.
    work_dir = Path(effect.work_dir)

    if not tmux.io(adapter.available()):
        raise AgentNotAvailableError(f"{effect.agent_type.value} CLI is not available")

    if tmux.backend.has_session(effect.session_name):
        raise SessionAlreadyExistsError(f"Session {effect.session_name} already exists")

    # Isolate Claude Code state per launch so the agent's `.claude.json`
    # is not shared with any concurrently-running Claude Code instance
    # on the host (e.g. the user's editor session). Without this, two
    # Claude processes race on `~/.claude/.claude.json` writes and the
    # workspace-trust entry we plant gets clobbered, causing the agent
    # to hang at "Yes, I trust this folder" forever.
    assert_no_forbidden_agent_env(
        effect.session_env,
        context="LaunchEffect.session_env",
    )
    # ADR-DOE-AGENTS-004 R9: the launch surface is auth-blind.  The
    # caller's session_env is a NON-AUTH overlay; auth material comes
    # from the handler binder (runtime policies below), mirroring the
    # wire's typed `binding` field.
    assert_session_env_is_non_auth_overlay(
        effect.session_env,
        context="LaunchEffect.session_env",
    )
    agent_env_exports: dict[str, str] = dict(effect.session_env or {})
    if effect.agent_type == AgentType.CLAUDE:
        assert_no_forbidden_agent_env(
            tmux.claude_runtime_policy.bootstrap_exports,
            context="ClaudeRuntimePolicy.bootstrap_exports",
        )
        agent_home = tmux.claude_runtime_policy.agent_home
        if agent_home is None:
            # Default to a per-launch isolated home under the workdir so
            # state cannot leak between concurrent agent runs or with
            # the user's interactive Claude Code session.
            agent_home = work_dir / ".agent-home"
        trusted_workspaces = tmux.claude_runtime_policy.trusted_workspaces or (
            work_dir,
        )
        _prepare_claude_home(tmux, agent_home, trusted_workspaces)
        agent_env_exports.update(
            {
                "HOME": str(agent_home),
                "CLAUDE_HOME": str(agent_home / ".claude"),
                **tmux.claude_runtime_policy.bootstrap_exports,
            }
        )
    elif effect.agent_type == AgentType.CODEX:
        # R9: CODEX_HOME is binder configuration — the runtime policy
        # first, then the binder process env (the process that BOUND
        # this handler configured its own environment).  Never the
        # effect's session_env (rejected above).
        policy_home = tmux.codex_runtime_policy.codex_home
        codex_home = (
            str(policy_home)
            if policy_home is not None
            else os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
        )
        tmux.io(trust_workspace_in_codex_home(codex_home, work_dir))
        agent_env_exports["CODEX_HOME"] = codex_home

    active_mcp_servers: dict[str, str] = dict(mcp_servers or {})
    if effect.mcp_tools and (
        not active_mcp_servers or effect.mcp_server_name not in active_mcp_servers
    ):
        raise AgentLaunchError(
            "MCP tools must run inside the caller's doeff VM via "
            "mcp_server_loop; no in-VM MCP server URL was provided"
        )
    # Schema-only sessions carry no domain tools but still need .mcp.json
    # so the agent can reach the report_result server (ADR-DOE-AGENTS-005).
    if active_mcp_servers:
        _write_mcp_json(work_dir, active_mcp_servers)

    # Disable oh-my-zsh's auto-update prompt. Without isolated HOME the
    # user's `.zshrc` would suppress this, but with `HOME=<work_dir>/
    # .agent-home` the agent's shell starts without that config and omz
    # blocks on its `[Y/n]` prompt — eating the first character of the
    # launch command we send next.
    session_env: dict[str, str] = dict(effect.session_env or {})
    if effect.agent_type == AgentType.CLAUDE:
        session_env["DISABLE_AUTO_UPDATE"] = "true"
        session_env["DISABLE_UPDATE_PROMPT"] = "true"

    tmux_config = tmux_transport.SessionConfig(
        session_name=effect.session_name,
        work_dir=work_dir,
        env=session_env or None,
    )
    session_info = tmux.backend.new_session(tmux_config)

    # Registration is bookkeeping independent of TUI readiness (issue
    # agentd-session-registration-after-ready-gate): the BOOTING row must
    # be externally observable the moment the physical session exists.
    # The ready gate below guards only prompt delivery; a failure past
    # this point transitions the row to a terminal state instead of
    # leaving an unobservable session behind.
    handle = SessionHandle(
        session_id=effect.session_name,
    )
    tmux.sessions[effect.session_name] = SessionState(
        handle=handle,
        adapter=adapter,
        pane_id=session_info.pane_id,
        agent_type=effect.agent_type,
        work_dir=work_dir,
        lifecycle=effect.lifecycle,
    )
    _record_snapshot(tmux, "session_started", handle, SessionStatus.BOOTING)

    try:
        argv = adapter.launch_command(
            LaunchParams(
                work_dir=work_dir,
                prompt=effect.prompt,
                model=effect.model,
                effort=effect.effort,
                bare=effect.bare,
                mcp_servers=active_mcp_servers or None,
            )
        )
        command = wrap_with_shell_exports(shlex.join(argv), agent_env_exports)

        tmux.backend.send_keys(session_info.pane_id, command, literal=False)
        # Dismiss onboarding dialogs (trust, theme, auth) if adapter supports
        # them. The first task prompt must be typed into the running agent,
        # not passed through argv/stdin. Handle startup UI before sending
        # that prompt.
        onboarding_patterns = getattr(adapter, "onboarding_patterns", None)
        if onboarding_patterns:
            _dismiss_onboarding_dialogs(
                session_info.pane_id,
                onboarding_patterns,
                timeout=effect.ready_timeout,
                backend=tmux.backend,
            )

        if adapter.injection_method == InjectionMethod.TMUX:
            deliver_prompt_when_ready(
                tmux.backend,
                session_info.pane_id,
                adapter,
                effect.prompt,
                session_name=effect.session_name,
                ready_timeout=effect.ready_timeout,
                timeout_error=AgentReadyTimeoutError,
            )
    except Exception as error:
        _fail_registered_launch(tmux, handle, effect.session_name, error)
        raise

    return handle


def _claude_launch(tmux: _TmuxAgentState, effect: ClaudeLaunchEffect) -> SessionHandle:
    """Launch a Claude-specific task with dedicated home/bootstrap handling."""
    adapter = get_adapter(AgentType.CLAUDE)
    if not tmux.io(adapter.available()):
        raise AgentNotAvailableError("claude CLI is not available")

    if tmux.backend.has_session(effect.session_name):
        raise SessionAlreadyExistsError(f"Session {effect.session_name} already exists")

    assert_no_forbidden_agent_env(
        effect.session_env,
        context="ClaudeLaunchEffect.session_env",
    )
    assert_session_env_is_non_auth_overlay(
        effect.session_env,
        context="ClaudeLaunchEffect.session_env",
    )
    assert_no_forbidden_agent_env(
        tmux.claude_runtime_policy.bootstrap_exports,
        context="ClaudeRuntimePolicy.bootstrap_exports",
    )
    agent_home = tmux.claude_runtime_policy.agent_home or Path.home()
    trusted_workspaces = tmux.claude_runtime_policy.trusted_workspaces or (effect.work_dir,)
    _prepare_claude_home(tmux, agent_home, trusted_workspaces)

    tmux_config = tmux_transport.SessionConfig(
        session_name=effect.session_name,
        work_dir=effect.work_dir,
        env=effect.session_env,
    )
    session_info = tmux.backend.new_session(tmux_config)

    # Same registration-before-ready-gate contract as _launch
    # (issue agentd-session-registration-after-ready-gate).
    handle = SessionHandle(
        session_id=effect.session_name,
    )
    tmux.sessions[effect.session_name] = SessionState(
        handle=handle,
        adapter=adapter,
        pane_id=session_info.pane_id,
        agent_type=AgentType.CLAUDE,
        work_dir=effect.work_dir,
        lifecycle=effect.lifecycle,
    )
    _record_snapshot(tmux, "session_started", handle, SessionStatus.BOOTING)

    try:
        argv = adapter.launch_command(
            LaunchParams(
                work_dir=effect.work_dir,
                prompt=effect.prompt,
                model=effect.model,
                effort=effect.effort,
                bare=effect.bare,
            )
        )
        command = wrap_with_shell_exports(
            shlex.join(argv),
            {
                **(effect.session_env or {}),
                "HOME": str(agent_home),
                "CLAUDE_HOME": str(agent_home / ".claude"),
                **tmux.claude_runtime_policy.bootstrap_exports,
            },
        )
        tmux.backend.send_keys(session_info.pane_id, command, literal=False)

        onboarding_patterns = getattr(adapter, "onboarding_patterns", None)
        if onboarding_patterns:
            _dismiss_onboarding_dialogs(
                session_info.pane_id,
                onboarding_patterns,
                timeout=effect.ready_timeout,
                backend=tmux.backend,
            )
        if adapter.injection_method == InjectionMethod.TMUX:
            deliver_prompt_when_ready(
                tmux.backend,
                session_info.pane_id,
                adapter,
                effect.prompt,
                session_name=effect.session_name,
                ready_timeout=effect.ready_timeout,
                timeout_error=AgentReadyTimeoutError,
            )
    except Exception as error:
        _fail_registered_launch(tmux, handle, effect.session_name, error)
        raise

    return handle


def _monitor(tmux: _TmuxAgentState, effect: MonitorEffect) -> Observation:
    """Check session status and return observation."""
    handle = effect.handle
    state = _state_for_handle(tmux, handle)

    if state is None:
        if not tmux.backend.has_session(handle.session_id):
            _record_snapshot(tmux, "session_exited", handle, SessionStatus.EXITED)
            return Observation(status=SessionStatus.EXITED)
        raise SessionNotFoundError(f"Session {handle.session_id} is not registered")

    if not tmux.backend.has_session(handle.session_id):
        state.status = SessionStatus.EXITED
        _record_snapshot(tmux, "session_exited", handle, SessionStatus.EXITED)
        return Observation(status=SessionStatus.EXITED)

    output = tmux.backend.capture_pane(state.pane_id)

    skip_lines = 5
    if hasattr(state.adapter, "status_bar_lines"):
        skip_lines = state.adapter.status_bar_lines

    content_hash = hash_content(output, skip_lines)
    output_changed = content_hash != state.monitor_state.output_hash
    has_prompt = is_waiting_for_input(output)

    if output_changed:
        state.monitor_state.output_hash = content_hash
        state.monitor_state.last_output = output
        state.monitor_state.last_output_at = datetime.now(timezone.utc)

    if (
        state.agent_type == AgentType.CLAUDE
        and _claude_mcp_permission_prompt_visible(output)
        and content_hash not in state.dismissed_permission_prompt_hashes
    ):
        tmux.backend.send_keys(state.pane_id, "", literal=True, enter=True)
        state.dismissed_permission_prompt_hashes.add(content_hash)
        _record_snapshot(
            tmux,
            "claude_mcp_permission_prompt_dismissed",
            handle,
            state.status,
            output_snippet=output[-500:] if output else None,
        )

    state.status = evolve_status(
        state.status,
        detect_status(output, state.monitor_state, output_changed, has_prompt),
        has_prompt=has_prompt,
    )

    observation = Observation(
        status=state.status,
        output_changed=output_changed,
        output_snippet=output[-500:] if output else None,
    )
    _record_snapshot(
        tmux,
        "session_observed",
        handle,
        state.status,
        output_snippet=observation.output_snippet,
    )
    return observation


def _capture(tmux: _TmuxAgentState, effect: CaptureEffect) -> str:
    """Capture pane output."""
    handle = effect.handle
    state = _state_for_handle(tmux, handle)
    if state is None:
        raise SessionNotFoundError(f"Session {handle.session_id} is not registered")
    if not tmux.backend.has_session(handle.session_id):
        raise SessionNotFoundError(f"Session {handle.session_id} does not exist")
    output = tmux.backend.capture_pane(state.pane_id, effect.lines)
    _record_snapshot(
        tmux,
        "session_captured",
        handle,
        state.status,
        output_snippet=output[-500:] if output else None,
    )
    return output


def _send(tmux: _TmuxAgentState, effect: SendEffect) -> None:
    """Send message to session."""
    handle = effect.handle
    state = _state_for_handle(tmux, handle)
    if state is None:
        raise SessionNotFoundError(f"Session {handle.session_id} is not registered")
    if not tmux.backend.has_session(handle.session_id):
        raise SessionNotFoundError(f"Session {handle.session_id} does not exist")
    tmux.backend.send_keys(
        state.pane_id,
        effect.message,
        literal=effect.literal,
        enter=effect.enter,
    )


def _stop(tmux: _TmuxAgentState, effect: StopEffect) -> None:
    """Stop session."""
    handle = effect.handle
    if tmux.backend.has_session(handle.session_id):
        tmux.backend.kill_session(handle.session_id)
    state = tmux.sessions.get(handle.session_id)
    if state:
        state.status = SessionStatus.STOPPED
    _record_snapshot(tmux, "session_stopped", handle, SessionStatus.STOPPED)


def _get_session(
    tmux: _TmuxAgentState,
    effect: GetAgentSessionEffect,
) -> AgentSessionSnapshot | None:
    """Return persisted session state without touching the backend."""
    return tmux.session_repository.get_session(effect.session_id)


def _list_sessions(
    tmux: _TmuxAgentState,
    effect: ListAgentSessionsEffect,
) -> tuple[AgentSessionSnapshot, ...]:
    """Return persisted sessions matching the query."""
    return tmux.session_repository.list_sessions(effect.query)


def _observe_session(
    tmux: _TmuxAgentState,
    effect: ObserveAgentSessionEffect,
) -> AgentSessionSnapshot:
    """Observe a session by persisted id and return updated state."""
    snapshot = _require_snapshot(tmux, effect.session_id)
    handle = snapshot.to_handle()
    observation = _monitor(tmux, MonitorEffect(handle=handle))
    updated = tmux.session_repository.get_session(effect.session_id)
    if updated is None:
        return _snapshot_from_observation(handle, observation)
    return updated


def _attach_session(tmux: _TmuxAgentState, effect: AttachAgentSessionEffect) -> None:
    """Attach to a session by persisted id."""
    snapshot = _require_snapshot(tmux, effect.session_id)
    if not tmux.backend.has_session(snapshot.session_name):
        raise SessionNotFoundError(f"Session {effect.session_id} does not exist")
    tmux.backend.attach_session(snapshot.session_name)


def _cancel_session(
    tmux: _TmuxAgentState,
    effect: CancelAgentSessionEffect,
) -> AgentSessionSnapshot:
    """Cancel a session by persisted id."""
    snapshot = _require_snapshot(tmux, effect.session_id)
    handle = snapshot.to_handle()
    _stop(tmux, StopEffect(handle=handle))
    updated = tmux.session_repository.get_session(effect.session_id)
    if updated is None:
        return _record_snapshot(tmux, "session_cancelled", handle, SessionStatus.STOPPED)
    return updated


def _cleanup_session(
    tmux: _TmuxAgentState,
    effect: CleanupAgentSessionEffect,
) -> AgentSessionSnapshot:
    """Clean up a session by persisted id."""
    snapshot = _require_snapshot(tmux, effect.session_id)
    handle = snapshot.to_handle()
    if tmux.backend.has_session(handle.session_id):
        tmux.backend.kill_session(handle.session_id)
    now = datetime.now(timezone.utc)
    cleaned = snapshot.with_update(
        status=SessionStatus.STOPPED,
        cleaned_at=now,
        last_observed_at=now,
    )
    tmux.sessions.pop(handle.session_id, None)
    _discard_result_sink(tmux, handle.session_id)
    return tmux.session_repository.record_snapshot(
        "session_cleaned",
        cleaned,
    )


def _launch_session(
    tmux: _TmuxAgentState,
    effect: LaunchSessionEffect,
    mcp_servers: dict[str, str] | None = None,
) -> SessionHandle:
    """Idempotently launch or re-adopt an L2 session."""
    session_id = effect.spec.session_id
    if session_id in tmux.sessions or tmux.session_repository.get_session(session_id):
        return SessionHandle(session_id=session_id)
    # ADR-DOE-AGENTS-005 R5: a schema session without a registered sink has
    # no result transport on the in-process face — the L2 defhandler path
    # registers the sink and serves report_result before launching. Direct
    # calls (including the L1 agent() task path, whose synchronous await
    # would starve the in-VM server loop) must fail fast, not launch an
    # agent whose result can never be read.
    if effect.spec.result_schema is not None and session_id not in tmux.result_sinks:
        raise AgentLaunchError(
            f"schema session {session_id} has no report_result sink: launch "
            "in-process schema sessions through the L2 LaunchSession "
            "defhandler path (ADR-DOE-AGENTS-005 R1/R5)"
        )
    report_result_server = (
        effect.spec.mcp_server_name
        if effect.spec.result_schema is not None
        else None
    )
    _launch(
        tmux,
        _launch_effect_from_spec(effect.spec, report_result_server),
        mcp_servers=mcp_servers,
    )
    state = tmux.sessions[session_id]
    state.result_schema = effect.spec.result_schema
    _record_snapshot(tmux, "session_l2_launched", state.handle, state.status)
    return SessionHandle(session_id=session_id)


def _await_result(tmux: _TmuxAgentState, effect: AwaitResultEffect) -> AwaitOutcome:
    """Await a reported result or an awaiting-input/timeout observation.

    The reported sink is the ONLY result source (ADR-DOE-AGENTS-005 R2):
    terminal bytes are never parsed as result payload; pane capture stays
    observation-only (status, dialogs, watchdogs). The per-await budget is
    the transport keep-alive heartbeat (L-K4-3): expiry only hands control
    back to the caller's re-await loop and never decides anything about
    the node.
    """
    timeout_seconds = (
        effect.timeout_seconds
        if effect.timeout_seconds is not None
        else DEFAULT_AWAIT_BUDGET_SECONDS
    )
    deadline = time.monotonic() + timeout_seconds
    while True:
        state = _state_for_handle(tmux, effect.handle)
        if state is None:
            raise SessionNotFoundError(f"Session {effect.handle.session_id} is not registered")

        # Result-first read of the typed report_result transport: the sink
        # payload is byte-faithful (never rendered by the TUI), already
        # schema-validated at report time, and wins regardless of session
        # liveness (ADR-DOE-AGENTS-002 R4 / ADR-DOE-AGENTS-005 R2).
        sink = tmux.result_sinks.get(effect.handle.session_id)
        if sink is not None and sink["payload"] is not None:
            return AwaitOutcome(status=AwaitStatus.EXITED, result=sink["payload"])

        if not tmux.backend.has_session(effect.handle.session_id):
            return _await_outcome_without_result()

        observation = _monitor(tmux, MonitorEffect(handle=effect.handle))
        if observation.status in (SessionStatus.BLOCKED, SessionStatus.BLOCKED_API):
            return AwaitOutcome(
                status=AwaitStatus.AWAITING_INPUT,
                validation_error=observation.output_snippet or "agent is awaiting input",
            )
        terminal_without_live_session = (
            observation.status == SessionStatus.EXITED
            and not tmux.backend.has_session(effect.handle.session_id)
        )
        if observation.is_terminal and (
            observation.status != SessionStatus.EXITED or terminal_without_live_session
        ):
            return _await_outcome_without_result()
        if time.monotonic() >= deadline:
            return AwaitOutcome(status=AwaitStatus.TIMED_OUT)
        time.sleep(0.2)


def _follow_up(tmux: _TmuxAgentState, effect: FollowUpEffect) -> SessionHandle:
    """Send validation feedback into the live session."""
    refuse_turn_capabilities(effect, handler="tmux_agent_defhandler")
    _send(
        tmux,
        SendEffect(
            handle=effect.handle,
            message=effect.message,
            enter=True,
            literal=True,
        ),
    )
    return effect.handle


def _stop_session(tmux: _TmuxAgentState, effect: StopSessionEffect) -> None:
    """Stop an L2 session."""
    _stop(tmux, StopEffect(handle=effect.handle))


def _release_session(tmux: _TmuxAgentState, effect: ReleaseSessionEffect) -> None:
    """Release handler-private state for an L2 session."""
    tmux.sessions.pop(effect.handle.session_id, None)
    _discard_result_sink(tmux, effect.handle.session_id)


def _require_snapshot(tmux: _TmuxAgentState, session_id: str) -> AgentSessionSnapshot:
    snapshot = tmux.session_repository.get_session(session_id)
    if snapshot is None:
        raise SessionNotFoundError(f"Session {session_id} is not registered")
    return snapshot


def _await_outcome_without_result() -> AwaitOutcome:
    """Typed no-result observation for a session that ended unreported.

    ADR-DOE-AGENTS-005 R4: the session reached a terminal state without
    calling ``report_result``. This is an observation, not a parse — the
    caller owns the solicitation/failure policy. ``continuable=False``
    because the pane is gone; a follow-up would land on a dead session.
    """
    return AwaitOutcome(
        status=AwaitStatus.EXITED,
        validation_error=(
            "session ended without reporting a result via the "
            f"`{REPORT_RESULT_TOOL_NAME}` MCP tool"
        ),
        continuable=False,
    )


def _snapshot_from_observation(
    handle: SessionHandle,
    observation: Observation,
) -> AgentSessionSnapshot:
    now = datetime.now(timezone.utc)
    return AgentSessionSnapshot.from_handle(
        handle,
        status=observation.status,
        last_observed_at=now,
        finished_at=now if observation.is_terminal else None,
        output_snippet=observation.output_snippet,
    )


def _fail_registered_launch(
    tmux: _TmuxAgentState,
    handle: SessionHandle,
    session_name: str,
    error: Exception,
) -> None:
    """Transition a registered launch that failed before completion.

    Once the BOOTING row exists (registration precedes the ready gate,
    issue agentd-session-registration-after-ready-gate), every launch
    failure must land the row in a terminal state — a lingering BOOTING
    row would be the new form of the old "unobservable session" leak.

    Terminal-first (order adopted from PR #542's review): the FAILED
    snapshot is persisted BEFORE the cleanup attempt, so a failing kill
    can neither skip terminalization nor mask the original launch error
    (the caller re-raises it). Cleanup success is recorded as
    cleaned_at; a failed cleanup leaves cleaned_at unset on an
    already-terminal row.
    """
    import logging

    _record_snapshot(
        tmux,
        "session_failed",
        handle,
        SessionStatus.FAILED,
        output_snippet=str(error),
    )
    try:
        if tmux.backend.has_session(session_name):
            tmux.backend.kill_session(session_name)
    except Exception as cleanup_error:
        logging.getLogger("doeff_agents.launch").warning(
            "ready-timeout cleanup failed for %s: %s "
            "(row already terminal; cleaned_at left unset)",
            session_name,
            cleanup_error,
        )
        return
    _record_snapshot(
        tmux,
        "session_cleaned",
        handle,
        SessionStatus.FAILED,
        output_snippet=str(error),
        cleaned_at=datetime.now(timezone.utc),
    )


def _record_snapshot(
    tmux: _TmuxAgentState,
    event_type: str,
    handle: SessionHandle,
    status: SessionStatus,
    *,
    output_snippet: str | None = None,
    cleaned_at: datetime | None = None,
) -> AgentSessionSnapshot:
    now = datetime.now(timezone.utc)
    previous = tmux.session_repository.get_session(handle.session_id)
    state = _state_for_handle(tmux, handle)
    backend_ref = dict(previous.backend_ref) if previous is not None else {}
    if state is not None:
        backend_ref.update(
            {
                "session_name": handle.session_id,
                "pane_id": state.pane_id,
                "agent_type": state.agent_type.value,
                "work_dir": str(state.work_dir),
            }
        )
        if state.result_schema is not None:
            backend_ref["result_schema_json"] = json.dumps(
                state.result_schema,
                sort_keys=True,
            )
    snapshot = AgentSessionSnapshot.from_handle(
        handle,
        status=status,
        backend_ref=backend_ref,
        lifecycle=state.lifecycle if state is not None else None,
        last_observed_at=now,
        finished_at=(
            previous.finished_at
            if previous is not None and previous.finished_at is not None
            else now
            if status
            in (
                SessionStatus.DONE,
                SessionStatus.FAILED,
                SessionStatus.EXITED,
                SessionStatus.STOPPED,
            )
            else None
        ),
        cleaned_at=(
            cleaned_at
            if cleaned_at is not None
            else previous.cleaned_at
            if previous is not None
            else None
        ),
        output_snippet=(
            output_snippet
            if output_snippet is not None
            else previous.output_snippet
            if previous is not None
            else None
        ),
    )
    return tmux.session_repository.record_snapshot(event_type, snapshot)


def _state_for_handle(tmux: _TmuxAgentState, handle: SessionHandle) -> SessionState | None:
    state = tmux.sessions.get(handle.session_id)
    if state is not None:
        return state
    snapshot = tmux.session_repository.get_session(handle.session_id)
    if snapshot is None:
        return None
    pane_id = snapshot.backend_ref.get("pane_id")
    if pane_id is None:
        return None
    state = SessionState(
        handle=handle,
        adapter=get_adapter(snapshot.agent_type),
        pane_id=pane_id,
        agent_type=snapshot.agent_type,
        work_dir=snapshot.work_dir,
        lifecycle=snapshot.lifecycle,
        started_at=snapshot.started_at,
        status=snapshot.status,
        result_schema=(
            json.loads(snapshot.backend_ref["result_schema_json"])
            if "result_schema_json" in snapshot.backend_ref
            else None
        ),
    )
    tmux.sessions[handle.session_id] = state
    return state


def _write_mcp_json(work_dir: Path, mcp_servers: dict[str, str]) -> None:
    """Write MCP config for clients that load `.mcp.json` from the workdir."""
    work_dir.mkdir(parents=True, exist_ok=True)
    mcp_json_path = work_dir / ".mcp.json"
    mcp_config = {
        "mcpServers": {
            name: {
                "type": "sse",
                "url": url,
            }
            for name, url in sorted(mcp_servers.items())
        },
    }
    mcp_json_path.write_text(json.dumps(mcp_config, indent=2))


def _prepare_claude_home(
    tmux: _TmuxAgentState,
    agent_home: Path,
    trusted_workspaces: tuple[Path, ...],
) -> None:
    tmux.io(prepare_claude_home(agent_home, trusted_workspaces))


def _default_io_root() -> IoRoot:
    from doeff_agents._io_handlers import run_driver_io

    return run_driver_io
