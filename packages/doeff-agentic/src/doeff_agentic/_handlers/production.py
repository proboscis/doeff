"""
Effect handler for agentic workflows.

This module provides the handler that interprets high-level agentic effects
by orchestrating doeff-agents sessions with state management and observability.

The handler:
1. Translates RunAgent/SendMessage/etc. to doeff-agents' Launch/Monitor/Capture/Send/Stop
   effects, which the agent runtime handler outside it answers
2. Manages workflow state files for CLI/plugin consumers
3. Integrates with doeff-flow for observability
"""


import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doeff_agents import (
    AgentType,
    Capture,
    Launch,
    Monitor,
    Send,
    SessionHandle,
    SessionStatus,
    Stop,
)
from doeff_agents.effects import LaunchEffect
from doeff_time import Delay

from doeff import Effect, Pass, Resume, do, handler, stacked_handlers
from doeff.program import ProgramHandler
from doeff_agentic._effects import (
    AgentNotRunningError,
    CaptureOutputEffect,
    RunAgentEffect,
    SendMessageEffect,
    StopAgentEffect,
    UserInputTimeoutError,
    WaitForStatusEffect,
    WaitForUserInputEffect,
)
from doeff_agentic._exceptions import AgenticUnsupportedOperationError
from doeff_agentic._state import StateManager, generate_workflow_id
from doeff_agentic._types import (
    AgentConfig,
    AgentInfo,
    AgentStatus,
    WorkflowInfo,
    WorkflowStatus,
)


def _agent_type_from_str(s: str) -> AgentType:
    """Convert string to AgentType enum."""
    s = s.lower()
    if s == "claude":
        return AgentType.CLAUDE
    if s == "codex":
        return AgentType.CODEX
    if s == "gemini":
        return AgentType.GEMINI
    return AgentType.CUSTOM


_AGENT_STATUS_BY_SESSION_STATUS = {
    SessionStatus.BOOTING: AgentStatus.BOOTING,
    SessionStatus.RUNNING: AgentStatus.RUNNING,
    SessionStatus.BLOCKED: AgentStatus.BLOCKED,
    SessionStatus.DONE: AgentStatus.DONE,
    SessionStatus.FAILED: AgentStatus.FAILED,
    SessionStatus.EXITED: AgentStatus.EXITED,
    SessionStatus.STOPPED: AgentStatus.STOPPED,
}

_SESSION_STATUS_BY_AGENT_STATUS = {
    agent_status: session_status
    for session_status, agent_status in _AGENT_STATUS_BY_SESSION_STATUS.items()
    if agent_status != AgentStatus.BOOTING
}


def _agent_status_from_session_status(s: SessionStatus) -> AgentStatus:
    """Convert SessionStatus to AgentStatus."""
    return _AGENT_STATUS_BY_SESSION_STATUS.get(s, AgentStatus.RUNNING)


def _launch_effect(session_name: str, config: AgentConfig, ready_timeout: float) -> LaunchEffect:
    """Lower a legacy AgentConfig onto doeff-agents' flat Launch effect.

    doeff-agents dropped the LaunchConfig wrapper together with its ``resume``
    and ``profile`` fields (26db24dd); Launch has no way to express either, so a
    config that asks for them is refused instead of launching something else.
    """
    if config.resume:
        raise AgenticUnsupportedOperationError(
            "resume", "tmux", "doeff-agents Launch no longer supports resuming a session"
        )
    if config.profile is not None:
        raise AgenticUnsupportedOperationError(
            "profile", "tmux", "doeff-agents Launch no longer takes a profile"
        )
    return Launch(
        session_name,
        agent_type=_agent_type_from_str(config.agent_type),
        work_dir=Path(config.work_dir) if config.work_dir else Path.cwd(),
        prompt=config.prompt,
        ready_timeout=ready_timeout,
    )


@dataclass
class _WorkflowContext:
    """The running workflow one ``agentic_effectful_handlers()`` handler keeps in its closure.

    Tracks the workflow state and active agent sessions.
    """

    workflow_id: str
    workflow_name: str
    state_manager: StateManager
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sessions: dict[str, SessionHandle] = field(default_factory=dict)
    # SessionHandle is opaque (session_id only) since doeff-agents 573fa5db, so
    # the launch time the state files report is recorded here.
    session_started_at: dict[str, datetime] = field(default_factory=dict)
    agent_counter: int = field(default=0)


def _session_name_for(ctx: _WorkflowContext, name: str | None) -> str:
    """Generate session name for an agent."""
    if name:
        return f"doeff-{ctx.workflow_id}-{name}"
    ctx.agent_counter += 1
    return f"doeff-{ctx.workflow_id}-agent-{ctx.agent_counter}"


def _register_session(ctx: _WorkflowContext, name: str, handle: SessionHandle) -> None:
    ctx.sessions[name] = handle
    ctx.session_started_at[name] = datetime.now(timezone.utc)


def _resolve_session_handle(ctx: _WorkflowContext, session_name: str) -> SessionHandle:
    handle = ctx.sessions.get(session_name)
    if handle is not None:
        return handle

    for name, candidate in ctx.sessions.items():
        if session_name in (candidate.session_id, name):
            return candidate

    raise AgentNotRunningError(session_name, "not_found")


def _write_workflow_meta(
    ctx: _WorkflowContext,
    status: WorkflowStatus,
    agents: tuple[AgentInfo, ...],
    current_agent: str | None = None,
    last_slog: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Write the workflow's state files, which the CLI and the TUI read."""
    ctx.state_manager.write_workflow_meta(
        WorkflowInfo(
            id=ctx.workflow_id,
            name=ctx.workflow_name,
            status=status,
            started_at=ctx.started_at,
            updated_at=datetime.now(timezone.utc),
            current_agent=current_agent,
            agents=agents,
            last_slog=last_slog,
            error=error,
        )
    )
    for agent in agents:
        ctx.state_manager.write_agent_state(ctx.workflow_id, agent)


@do
def _agent_info(ctx: _WorkflowContext, name: str, handle: SessionHandle):
    """The state-file view of one session, observed through the agent runtime (Monitor)."""
    try:
        obs = yield Monitor(handle)
        agent_status = _agent_status_from_session_status(obs.status)
    except Exception:
        agent_status = AgentStatus.EXITED

    return AgentInfo(
        name=name,
        status=agent_status,
        session_name=handle.session_id,
        started_at=ctx.session_started_at.get(name, ctx.started_at),
    )


@do
def _update_workflow_status(
    ctx: _WorkflowContext,
    status: WorkflowStatus,
    current_agent: str | None = None,
    last_slog: dict[str, Any] | None = None,
    error: str | None = None,
):
    """Observe every session and write the workflow's state files."""
    agents: tuple[AgentInfo, ...] = ()
    # Not spawned side by side: the handler runs without a scheduler in the stack
    # (plain run() in tests), and Spawn / Gather would require one.
    for name, handle in list(ctx.sessions.items()):
        agents = (*agents, (yield _agent_info(ctx, name, handle)))
    _write_workflow_meta(ctx, status, agents, current_agent, last_slog, error)


# =============================================================================
# Answers (one per effect — each returns the Program whose value resumes k)
# =============================================================================


@do
def _run_agent(ctx: _WorkflowContext, effect: RunAgentEffect):
    """Launch an agent, monitor it until it ends, and return its final output."""
    agent_name = effect.session_name or f"agent-{ctx.agent_counter + 1}"
    session_name = _session_name_for(ctx, effect.session_name)

    handle = yield _launch_effect(session_name, effect.config, effect.ready_timeout)
    _register_session(ctx, agent_name, handle)

    yield _update_workflow_status(ctx, WorkflowStatus.RUNNING, current_agent=agent_name)

    while True:
        obs = yield Monitor(handle)

        if obs.output_changed or obs.status != SessionStatus.RUNNING:
            status = (
                WorkflowStatus.BLOCKED
                if obs.status == SessionStatus.BLOCKED
                else WorkflowStatus.RUNNING
            )
            yield _update_workflow_status(ctx, status, current_agent=agent_name)

        if obs.is_terminal:
            break

        yield Delay(effect.poll_interval)

    last_output = yield Capture(handle, lines=500)
    yield _update_workflow_status(ctx, WorkflowStatus.RUNNING, current_agent=None)
    return last_output


@do
def _send_message(ctx: _WorkflowContext, effect: SendMessageEffect):
    handle = _resolve_session_handle(ctx, effect.session_name)
    yield Send(handle, effect.message, enter=effect.enter)


@do
def _wait_for_status(ctx: _WorkflowContext, effect: WaitForStatusEffect):
    """Poll the session until it reaches a target status, ends, or the timeout passes."""
    handle = _resolve_session_handle(ctx, effect.session_name)
    targets = (
        (effect.target_status,)
        if isinstance(effect.target_status, AgentStatus)
        else effect.target_status
    )
    session_targets = {
        _SESSION_STATUS_BY_AGENT_STATUS[target]
        for target in targets
        if target in _SESSION_STATUS_BY_AGENT_STATUS
    }

    deadline = time.time() + effect.timeout
    while time.time() < deadline:
        obs = yield Monitor(handle)
        if obs.status in session_targets or obs.is_terminal:
            return _agent_status_from_session_status(obs.status)
        yield Delay(effect.poll_interval)

    obs = yield Monitor(handle)
    return _agent_status_from_session_status(obs.status)


@do
def _capture_output(ctx: _WorkflowContext, effect: CaptureOutputEffect):
    handle = _resolve_session_handle(ctx, effect.session_name)
    return (yield Capture(handle, lines=effect.lines))


def _write_input_request(ctx: _WorkflowContext, effect: WaitForUserInputEffect) -> tuple[Path, Path]:
    """Write the input request the CLI shows the user; return it and the response file the CLI writes."""
    workflow_dir = ctx.state_manager.state_dir / "workflows" / ctx.workflow_id
    input_request_file = workflow_dir / "input_request.json"
    input_request_file.write_text(json.dumps({
        "session_name": effect.session_name,
        "prompt": effect.prompt,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }))
    return input_request_file, workflow_dir / "input_response.txt"


@do
def _wait_for_user_input(ctx: _WorkflowContext, effect: WaitForUserInputEffect):
    """Block the workflow until the user answers through the CLI or the TUI.

    The request is a marker file the CLI reads; the answer is the response file it
    writes. The poll waits with doeff_time Delay.
    """
    if effect.session_name not in ctx.sessions:
        raise AgentNotRunningError(effect.session_name, "not_found")

    yield _update_workflow_status(
        ctx,
        WorkflowStatus.BLOCKED,
        current_agent=effect.session_name,
        last_slog={"status": "waiting-input", "prompt": effect.prompt},
    )

    input_request_file, input_response_file = _write_input_request(ctx, effect)

    deadline = time.time() + effect.timeout if effect.timeout else None
    while not input_response_file.exists():
        if deadline and time.time() > deadline:
            input_request_file.unlink(missing_ok=True)
            raise UserInputTimeoutError("wait_for_user_input", effect.timeout or 0)
        yield Delay(0.5)

    response = input_response_file.read_text()
    input_response_file.unlink()
    input_request_file.unlink(missing_ok=True)

    yield _update_workflow_status(ctx, WorkflowStatus.RUNNING, current_agent=effect.session_name)
    return response


@do
def _stop_agent(ctx: _WorkflowContext, effect: StopAgentEffect):
    handle = ctx.sessions.get(effect.session_name)
    if handle is None:
        return None  # Already stopped or not found

    yield Stop(handle)
    del ctx.sessions[effect.session_name]
    yield _update_workflow_status(ctx, WorkflowStatus.RUNNING)
    return None


def agentic_effectful_handlers(
    workflow_id: str | None = None,
    workflow_name: str | None = None,
    state_dir: Path | str | None = None,
    agent_runtime: ProgramHandler | None = None,
) -> ProgramHandler:
    """Return the doeff handler for the legacy agentic effects (RunAgent, SendMessage, ...).

    It installs two handlers: the agentic handler inside, and the agent runtime
    outside it. The agentic handler lowers its effects onto doeff-agents'
    Launch / Monitor / Capture / Send / Stop by yielding them; effects a handler
    yields go to the handlers *outside* it, so the runtime answers them. The
    runtime also serves those effects when the program yields them directly.

    The workflow (its id, sessions and state files) lives in the handler's closure.
    Building the handler writes the workflow's state files as RUNNING.

    Args:
        workflow_id: Workflow identifier (auto-generated if not provided)
        workflow_name: Human-readable workflow name
        state_dir: Directory for state files
        agent_runtime: The handler answering doeff-agents' Launch / Monitor /
            Capture / Send / Stop (default: doeff-agents' tmux handler
            ``agent_effectful_handler()``, which reads its session backend with
            ``Ask(SessionBackend)``)

    Poll waits are yielded as doeff_time ``Delay``, so the caller's stack needs a
    time handler outside both.
    """
    if agent_runtime is None:
        from doeff_agents.handlers import agent_effectful_handler

        agent_runtime = agent_effectful_handler()

    name = workflow_name or "unnamed"
    ctx = _WorkflowContext(
        workflow_id=workflow_id or generate_workflow_id(name),
        workflow_name=name,
        state_manager=StateManager(state_dir),
    )
    _write_workflow_meta(ctx, WorkflowStatus.RUNNING, agents=())

    @handler
    def answer_agentic(effect: Effect, k: Any):
        """Answer a legacy agentic effect through the agent runtime; pass every other effect outward."""
        match effect:
            case RunAgentEffect():
                answer = _run_agent(ctx, effect)
            case SendMessageEffect():
                answer = _send_message(ctx, effect)
            case WaitForStatusEffect():
                answer = _wait_for_status(ctx, effect)
            case CaptureOutputEffect():
                answer = _capture_output(ctx, effect)
            case WaitForUserInputEffect():
                answer = _wait_for_user_input(ctx, effect)
            case StopAgentEffect():
                answer = _stop_agent(ctx, effect)
            case _:
                return (yield Pass(effect, k))
        value = yield answer
        return (yield Resume(k, value))

    return stacked_handlers(agent_runtime, answer_agentic)


def with_agentic_effectful_handlers(
    program: Any,
    workflow_id: str | None = None,
    workflow_name: str | None = None,
    state_dir: Path | str | None = None,
    agent_runtime: ProgramHandler | None = None,
) -> Any:
    """Wrap a program with the legacy agentic effect handler."""
    return agentic_effectful_handlers(
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        state_dir=state_dir,
        agent_runtime=agent_runtime,
    )(program)


__all__ = [
    "agentic_effectful_handlers",
    "with_agentic_effectful_handlers",
]
