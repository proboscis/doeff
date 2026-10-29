"""
Legacy tmux handler for doeff-agentic.

This module provides a handler that uses tmux for agent session management.
It has limited feature support compared to the OpenCode handler.

Limitations:
- Only SHARED environment type (no worktree, inherited, copy)
- No session forking (AgenticForkSession raises AgenticUnsupportedOperationError)
- Polling-based status detection (no SSE events)
- Use core Gather/Race effects for parallel execution

Usage:
    from doeff import run
    from doeff_agentic._tmux_handler import tmux_handler
    from doeff_core_effects._scheduler import scheduled

    handlers = tmux_handler()
    program = handlers(my_workflow())
    result = run(scheduled(program))
"""


import contextlib
import hashlib
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doeff import Effect, Pass, Resume, handler
from doeff.program import ProgramHandler
from doeff_agentic._effects import (
    AgenticAbortSession,
    AgenticCreateEnvironment,
    AgenticCreateSession,
    AgenticCreateWorkflow,
    AgenticDeleteEnvironment,
    AgenticDeleteSession,
    AgenticForkSession,
    AgenticGetEnvironment,
    AgenticGetMessages,
    AgenticGetSession,
    AgenticGetSessionStatus,
    AgenticGetWorkflow,
    AgenticNextEvent,
    AgenticSendMessage,
    AgenticSupportsCapability,
)
from doeff_agentic._event_log import EventLogWriter, WorkflowIndex
from doeff_agentic._exceptions import (
    AgenticDuplicateNameError,
    AgenticEnvironmentInUseError,
    AgenticEnvironmentNotFoundError,
    AgenticServerError,
    AgenticSessionNotFoundError,
    AgenticSessionNotRunningError,
    AgenticTimeoutError,
    AgenticUnsupportedOperationError,
)
from doeff_agentic._handlers._effect_groups import MessageEffect, SessionEffect, WorkflowEffect
from doeff_agentic._types import (
    AgenticEndOfEvents,
    AgenticEnvironmentHandle,
    AgenticEnvironmentType,
    AgenticEvent,
    AgenticMessage,
    AgenticMessageHandle,
    AgenticSessionHandle,
    AgenticSessionStatus,
    AgenticWorkflowHandle,
    AgenticWorkflowStatus,
)

# =============================================================================
# Tmux utilities (inline to avoid doeff-agents dependency)
# =============================================================================

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return ANSI_PATTERN.sub("", text)


def _resolve_default_backend():
    try:
        from doeff_agents._tmux import get_default_backend
    except ImportError as exc:
        raise AgenticServerError(
            "tmux backend requires doeff-agents. "
            "Install doeff-agentic[legacy] or add doeff-agents to the environment."
        ) from exc

    return get_default_backend()


def _session_config(session_name: str, work_dir: Path | None = None):
    try:
        from doeff_agents import SessionConfig
    except ImportError as exc:
        raise AgenticServerError(
            "tmux backend requires doeff-agents. "
            "Install doeff-agentic[legacy] or add doeff-agents to the environment."
        ) from exc

    return SessionConfig(session_name=session_name, work_dir=work_dir)


def is_tmux_available() -> bool:
    """Check if tmux is installed."""
    return _resolve_default_backend().is_available()


def has_session(name: str) -> bool:
    """Check if a tmux session exists."""
    return _resolve_default_backend().has_session(name)


def new_session(session_name: str, work_dir: Path | None = None) -> str:
    """Create a new detached tmux session. Returns pane_id."""
    return _resolve_default_backend().new_session(
        _session_config(session_name=session_name, work_dir=work_dir)
    ).pane_id


def send_keys(
    target: str, keys: str, *, literal: bool = True, enter: bool = True
) -> None:
    """Send keys to a tmux pane."""
    _resolve_default_backend().send_keys(target, keys, literal=literal, enter=enter)


def capture_pane(target: str, lines: int = 100) -> str:
    """Capture the content of a tmux pane."""
    return _resolve_default_backend().capture_pane(target, lines)


def kill_session(session: str) -> None:
    """Kill a tmux session."""
    _resolve_default_backend().kill_session(session)


def attach_session(session: str) -> None:
    """Attach to a tmux session."""
    _resolve_default_backend().attach_session(session)


# =============================================================================
# Status Detection
# =============================================================================


def detect_status(output: str) -> AgenticSessionStatus:
    """Detect session status from pane output."""
    lines = output.strip().split("\n")
    last_lines = "\n".join(lines[-10:]) if lines else ""

    # Check for completion patterns
    done_patterns = [
        r"Agent completed",
        r"Task completed",
        r"Session complete",
        r"\[done\]",
        r"Done\.",
        r"Goodbye!",
    ]
    for pattern in done_patterns:
        if re.search(pattern, last_lines, re.IGNORECASE):
            return AgenticSessionStatus.DONE

    # Check for error patterns
    error_patterns = [
        r"Error:",
        r"Exception:",
        r"Failed:",
        r"\[error\]",
        r"fatal:",
    ]
    for pattern in error_patterns:
        if re.search(pattern, last_lines, re.IGNORECASE):
            return AgenticSessionStatus.ERROR

    # Check for blocked/waiting patterns
    blocked_patterns = [
        r"Waiting for",
        r"Press Enter",
        r"\[y/n\]",
        r"\?$",
        r"> $",
        r">>> $",
    ]
    for pattern in blocked_patterns:
        if re.search(pattern, last_lines):
            return AgenticSessionStatus.BLOCKED

    # Default to running
    return AgenticSessionStatus.RUNNING

# =============================================================================
# State Management
# =============================================================================


@dataclass
class _TmuxSession:
    """State for a tmux-backed session."""

    handle: AgenticSessionHandle
    pane_id: str
    last_output_hash: str = ""
    message_history: tuple[dict[str, Any], ...] = ()


@dataclass
class _TmuxWorkflow:
    """In-memory state for a workflow."""

    id: str
    name: str | None
    status: AgenticWorkflowStatus
    created_at: datetime
    metadata: dict[str, Any] | None = None
    environments: dict[str, AgenticEnvironmentHandle] = field(default_factory=dict)
    sessions: dict[str, _TmuxSession] = field(default_factory=dict)  # name -> state
    session_by_id: dict[str, str] = field(default_factory=dict)  # session_id -> name


@dataclass
class _TmuxWorld:
    """The state one ``tmux_handler()`` handler keeps in its closure."""

    working_dir: Path
    backend: Any
    event_log: EventLogWriter
    workflow_index: WorkflowIndex
    workflow: _TmuxWorkflow | None = None


def generate_workflow_id(name: str | None = None) -> str:
    """Generate a 7-char hex workflow ID."""
    data = f"{name or 'workflow'}-{time.time()}"
    return hashlib.sha256(data.encode()).hexdigest()[:7]


def generate_session_id() -> str:
    """Generate a unique session ID."""
    return f"tmux_{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:12]}"


def generate_environment_id(name: str | None = None) -> str:
    """Generate environment ID."""
    data = f"env-{name or 'default'}-{time.time()}"
    return hashlib.sha256(data.encode()).hexdigest()[:8]


_SUPPORTED_CAPABILITIES = frozenset({"worktree"})  # worktree via git commands


# =============================================================================
# Helpers
# =============================================================================


def _tmux_session_name(workflow: _TmuxWorkflow, name: str) -> str:
    return f"doeff-{workflow.id}-{name}"


def _ensure_workflow(world: _TmuxWorld) -> _TmuxWorkflow:
    if world.workflow is None:
        _create_workflow(world, AgenticCreateWorkflow())
    assert world.workflow is not None
    return world.workflow


def _session_state(world: _TmuxWorld, session_id: str) -> tuple[_TmuxWorkflow, _TmuxSession]:
    workflow = _ensure_workflow(world)
    name = workflow.session_by_id.get(session_id)
    if not name:
        raise AgenticSessionNotFoundError(session_id)
    return workflow, workflow.sessions[name]


def _set_status(
    world: _TmuxWorld,
    workflow: _TmuxWorkflow,
    state: _TmuxSession,
    status: AgenticSessionStatus,
) -> None:
    """Record the session's new status, and log it when it changed."""
    old_status = state.handle.status
    state.handle = replace(state.handle, status=status)
    if old_status != status:
        world.event_log.log_session_status(workflow.id, state.handle.name, status)


def _create_worktree(world: _TmuxWorld, env_id: str, base_commit: str | None) -> str:
    """Create a git worktree."""
    worktree_dir = Path(f"/tmp/doeff/worktrees/{env_id}")
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["git", "worktree", "add", str(worktree_dir)]
    if base_commit:
        cmd.append(base_commit)
    else:
        cmd.extend(["--detach", "HEAD"])

    subprocess.run(cmd, cwd=world.working_dir, check=True, capture_output=True)
    return str(worktree_dir)


def _delete_worktree(world: _TmuxWorld, path: str) -> None:
    """Remove a git worktree."""
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", path],
            cwd=world.working_dir,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        shutil.rmtree(path, ignore_errors=True)


def _refresh_session_status(
    world: _TmuxWorld, workflow: _TmuxWorkflow, state: _TmuxSession
) -> None:
    """Refresh session status from tmux."""
    if not world.backend.has_session(_tmux_session_name(workflow, state.handle.name)):
        _set_status(world, workflow, state, AgenticSessionStatus.DONE)
        return

    try:
        output = world.backend.capture_pane(state.pane_id, 50)
    except Exception:
        return
    _set_status(world, workflow, state, detect_status(output))


def _wait_for_completion(
    world: _TmuxWorld, workflow: _TmuxWorkflow, state: _TmuxSession, timeout: float = 300.0
) -> None:
    """Wait for session to complete or become blocked."""
    session_name = _tmux_session_name(workflow, state.handle.name)
    start = time.time()

    while time.time() - start < timeout:
        if not world.backend.has_session(session_name):
            return

        _refresh_session_status(world, workflow, state)

        if state.handle.status.is_terminal() or state.handle.status == AgenticSessionStatus.BLOCKED:
            return

        time.sleep(1.0)


# =============================================================================
# Answers (one per effect)
# =============================================================================


def _create_workflow(world: _TmuxWorld, effect: AgenticCreateWorkflow) -> AgenticWorkflowHandle:
    workflow_id = generate_workflow_id(effect.name)
    world.workflow = _TmuxWorkflow(
        id=workflow_id,
        name=effect.name,
        status=AgenticWorkflowStatus.RUNNING,
        created_at=datetime.now(timezone.utc),
        metadata=effect.metadata,
    )

    world.event_log.log_workflow_created(workflow_id, effect.name, effect.metadata)
    world.workflow_index.add(workflow_id, effect.name)

    return AgenticWorkflowHandle(
        id=workflow_id,
        name=effect.name,
        status=AgenticWorkflowStatus.RUNNING,
        created_at=world.workflow.created_at,
        metadata=effect.metadata,
    )


def _get_workflow(world: _TmuxWorld, effect: AgenticGetWorkflow) -> AgenticWorkflowHandle:
    workflow = _ensure_workflow(world)
    return AgenticWorkflowHandle(
        id=workflow.id,
        name=workflow.name,
        status=workflow.status,
        created_at=workflow.created_at,
        metadata=workflow.metadata,
    )


def _create_environment(
    world: _TmuxWorld, effect: AgenticCreateEnvironment
) -> AgenticEnvironmentHandle:
    workflow = _ensure_workflow(world)
    env_id = generate_environment_id(effect.name)

    match effect.env_type:
        case AgenticEnvironmentType.SHARED:
            working_dir = effect.working_dir or str(world.working_dir)
        case AgenticEnvironmentType.WORKTREE:
            working_dir = _create_worktree(world, env_id, effect.base_commit)
        case _:
            raise AgenticUnsupportedOperationError(
                f"create_environment({effect.env_type.value})",
                "tmux",
                "Only SHARED and WORKTREE environment types are supported",
            )

    handle = AgenticEnvironmentHandle(
        id=env_id,
        env_type=effect.env_type,
        name=effect.name,
        working_dir=working_dir,
        created_at=datetime.now(timezone.utc),
        base_commit=effect.base_commit,
        source_environment_id=effect.source_environment_id,
    )
    workflow.environments[env_id] = handle
    world.event_log.log_environment_created(workflow.id, handle)
    return handle


def _get_environment(world: _TmuxWorld, effect: AgenticGetEnvironment) -> AgenticEnvironmentHandle:
    workflow = _ensure_workflow(world)
    handle = workflow.environments.get(effect.environment_id)
    if not handle:
        raise AgenticEnvironmentNotFoundError(effect.environment_id)
    return handle


def _delete_environment(world: _TmuxWorld, effect: AgenticDeleteEnvironment) -> bool:
    workflow = _ensure_workflow(world)
    handle = workflow.environments.get(effect.environment_id)
    if not handle:
        raise AgenticEnvironmentNotFoundError(effect.environment_id)

    using_sessions = [
        s.handle.name
        for s in workflow.sessions.values()
        if s.handle.environment_id == effect.environment_id
    ]
    if using_sessions and not effect.force:
        raise AgenticEnvironmentInUseError(effect.environment_id, using_sessions)

    if handle.env_type == AgenticEnvironmentType.WORKTREE:
        _delete_worktree(world, handle.working_dir)

    del workflow.environments[effect.environment_id]
    return True


def _create_session(world: _TmuxWorld, effect: AgenticCreateSession) -> AgenticSessionHandle:
    workflow = _ensure_workflow(world)

    if effect.name in workflow.sessions:
        raise AgenticDuplicateNameError(effect.name, workflow.id)

    if effect.environment_id:
        env = workflow.environments.get(effect.environment_id)
        if not env:
            raise AgenticEnvironmentNotFoundError(effect.environment_id)
        env_id = effect.environment_id
        work_dir = Path(env.working_dir)
    else:
        # Create implicit shared environment
        env_id = generate_environment_id("shared")
        workflow.environments[env_id] = AgenticEnvironmentHandle(
            id=env_id,
            env_type=AgenticEnvironmentType.SHARED,
            name="shared",
            working_dir=str(world.working_dir),
            created_at=datetime.now(timezone.utc),
        )
        work_dir = world.working_dir

    session_id = generate_session_id()
    try:
        pane_id = world.backend.new_session(
            _session_config(session_name=_tmux_session_name(workflow, effect.name), work_dir=work_dir)
        ).pane_id
    except Exception as e:
        raise AgenticServerError(f"Failed to create tmux session: {e}") from e

    handle = AgenticSessionHandle(
        id=session_id,
        name=effect.name,
        workflow_id=workflow.id,
        environment_id=env_id,
        status=AgenticSessionStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        title=effect.title or effect.name,
        agent=effect.agent,
        model=effect.model,
    )
    workflow.sessions[effect.name] = _TmuxSession(handle=handle, pane_id=pane_id)
    workflow.session_by_id[session_id] = effect.name

    world.event_log.log_session_created(workflow.id, handle)
    world.event_log.log_session_bound_to_environment(workflow.id, env_id, effect.name)
    return handle


def _get_session(world: _TmuxWorld, effect: AgenticGetSession) -> AgenticSessionHandle:
    workflow = _ensure_workflow(world)

    if effect.name:
        state = workflow.sessions.get(effect.name)
        if not state:
            raise AgenticSessionNotFoundError(effect.name, by_name=True)
        return state.handle

    if effect.session_id:
        return _session_state(world, effect.session_id)[1].handle

    raise ValueError("Either session_id or name must be provided")


def _abort_session(world: _TmuxWorld, effect: AgenticAbortSession) -> None:
    workflow, state = _session_state(world, effect.session_id)

    # Send Ctrl+C to abort
    with contextlib.suppress(Exception):
        world.backend.send_keys(state.pane_id, "C-c", literal=False, enter=False)

    world.backend.kill_session(_tmux_session_name(workflow, state.handle.name))
    state.handle = replace(state.handle, status=AgenticSessionStatus.ABORTED)


def _delete_session(world: _TmuxWorld, effect: AgenticDeleteSession) -> bool:
    workflow, state = _session_state(world, effect.session_id)
    name = state.handle.name

    world.backend.kill_session(_tmux_session_name(workflow, name))
    workflow.session_by_id.pop(effect.session_id, None)
    workflow.sessions.pop(name, None)
    return True


def _send_message(world: _TmuxWorld, effect: AgenticSendMessage) -> AgenticMessageHandle:
    workflow, state = _session_state(world, effect.session_id)
    name = state.handle.name

    if not world.backend.has_session(_tmux_session_name(workflow, name)):
        raise AgenticSessionNotRunningError(effect.session_id, state.handle.status.value)

    world.event_log.log_message_sent(workflow.id, name, effect.content, effect.wait)
    world.backend.send_keys(state.pane_id, effect.content, literal=True, enter=True)

    msg_id = f"msg_{time.time_ns()}"
    state.message_history = (
        *state.message_history,
        {
            "id": msg_id,
            "role": "user",
            "content": effect.content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    _set_status(world, workflow, state, AgenticSessionStatus.RUNNING)

    if effect.wait:
        # Poll until completion or blocked
        _wait_for_completion(world, workflow, state)
        world.event_log.log_message_complete(workflow.id, name)

    return AgenticMessageHandle(
        id=msg_id,
        session_id=effect.session_id,
        role="user",
        created_at=datetime.now(timezone.utc),
    )


def _get_messages(world: _TmuxWorld, effect: AgenticGetMessages) -> list[AgenticMessage]:
    _, state = _session_state(world, effect.session_id)

    try:
        output = world.backend.capture_pane(state.pane_id, 500)
    except Exception:
        output = ""

    history = [
        AgenticMessage(
            id=msg["id"],
            session_id=effect.session_id,
            role=msg["role"],
            content=msg["content"],
            created_at=datetime.fromisoformat(msg["created_at"]),
        )
        for msg in state.message_history
    ]
    # The current pane output reads as the assistant's message.
    current = (
        [
            AgenticMessage(
                id=f"output_{time.time_ns()}",
                session_id=effect.session_id,
                role="assistant",
                content=output,
                created_at=datetime.now(timezone.utc),
            )
        ]
        if output
        else []
    )
    messages = history + current

    if effect.limit:
        return messages[-effect.limit :]
    return messages


def _next_event(world: _TmuxWorld, effect: AgenticNextEvent) -> AgenticEvent | AgenticEndOfEvents:
    """Answer AgenticNextEvent by polling the pane."""
    workflow, state = _session_state(world, effect.session_id)
    session_name = _tmux_session_name(workflow, state.handle.name)

    start = time.time()
    while True:
        if effect.timeout and (time.time() - start) > effect.timeout:
            raise AgenticTimeoutError("next_event", effect.timeout)

        if not world.backend.has_session(session_name):
            return AgenticEndOfEvents(
                reason="session_done",
                final_status=AgenticSessionStatus.DONE,
            )

        try:
            output = world.backend.capture_pane(state.pane_id, 50)
        except Exception:
            return AgenticEndOfEvents(
                reason="session_error",
                final_status=AgenticSessionStatus.ERROR,
            )

        new_status = detect_status(output)
        if new_status != state.handle.status:
            old_status = state.handle.status
            state.handle = replace(state.handle, status=new_status)

            if new_status.is_terminal():
                return AgenticEndOfEvents(
                    reason=f"session_{new_status.value}",
                    final_status=new_status,
                )

            return AgenticEvent(
                event_type="session.status",
                session_id=effect.session_id,
                data={"old": old_status.value, "new": new_status.value},
                timestamp=datetime.now(timezone.utc),
            )

        output_hash = hashlib.sha256(output.encode()).hexdigest()[:16]
        if output_hash != state.last_output_hash:
            state.last_output_hash = output_hash
            return AgenticEvent(
                event_type="poll.output",
                session_id=effect.session_id,
                data={"snippet": output[-500:]},
                timestamp=datetime.now(timezone.utc),
            )

        time.sleep(1.0)


def _get_session_status(
    world: _TmuxWorld, effect: AgenticGetSessionStatus
) -> AgenticSessionStatus:
    workflow, state = _session_state(world, effect.session_id)
    _refresh_session_status(world, workflow, state)
    return state.handle.status


def _answer_workflow_effect(world: _TmuxWorld, effect: WorkflowEffect) -> object:
    """The tmux handler's answer to a workflow or environment effect."""
    match effect:
        case AgenticCreateWorkflow():
            return _create_workflow(world, effect)
        case AgenticGetWorkflow():
            return _get_workflow(world, effect)
        case AgenticCreateEnvironment():
            return _create_environment(world, effect)
        case AgenticGetEnvironment():
            return _get_environment(world, effect)
        case AgenticDeleteEnvironment():
            return _delete_environment(world, effect)


def _answer_session_effect(world: _TmuxWorld, effect: SessionEffect) -> object:
    """The tmux handler's answer to a session lifecycle effect (forking is refused)."""
    match effect:
        case AgenticCreateSession():
            return _create_session(world, effect)
        case AgenticForkSession():
            raise AgenticUnsupportedOperationError(
                "fork_session",
                "tmux",
                "Session forking is not supported with tmux backend",
            )
        case AgenticGetSession():
            return _get_session(world, effect)
        case AgenticAbortSession():
            return _abort_session(world, effect)
        case AgenticDeleteSession():
            return _delete_session(world, effect)


def _answer_message_effect(world: _TmuxWorld, effect: MessageEffect) -> object:
    """The tmux handler's answer to a message, event or status effect."""
    match effect:
        case AgenticSendMessage():
            return _send_message(world, effect)
        case AgenticGetMessages():
            return _get_messages(world, effect)
        case AgenticNextEvent():
            return _next_event(world, effect)
        case AgenticGetSessionStatus():
            return _get_session_status(world, effect)
        case AgenticSupportsCapability():
            return effect.capability in _SUPPORTED_CAPABILITIES


# =============================================================================
# Handler Factory
# =============================================================================


def tmux_handler(
    working_dir: str | None = None,
    *,
    backend: Any | None = None,
) -> ProgramHandler:
    """Return a doeff handler that answers the agentic effects with tmux sessions.

    The workflow, environments and sessions live in the handler's closure, so each
    call starts a fresh workflow. Effects it does not own go to the handlers
    outside it.

    Args:
        working_dir: Default working directory
        backend: The doeff-agents session backend (default: doeff-agents' default
            backend). Building the handler fails if tmux is not available.

    Usage:
        from doeff import run
        from doeff_agentic import tmux_handler
        from doeff_core_effects._scheduler import scheduled

        program = tmux_handler()(my_workflow())
        result = run(scheduled(program))
    """
    world = _TmuxWorld(
        working_dir=Path(working_dir) if working_dir else Path.cwd(),
        backend=backend or _resolve_default_backend(),
        event_log=EventLogWriter(),
        workflow_index=WorkflowIndex(),
    )
    if not world.backend.is_available():
        raise AgenticServerError("tmux is not installed or not in PATH")

    @handler
    def answer_tmux_agentic(effect: Effect, k: Any):
        """Answer an agentic effect from the tmux world; pass every other effect outward."""
        match effect:
            case (
                AgenticCreateWorkflow()
                | AgenticGetWorkflow()
                | AgenticCreateEnvironment()
                | AgenticGetEnvironment()
                | AgenticDeleteEnvironment()
            ):
                value = _answer_workflow_effect(world, effect)
            case (
                AgenticCreateSession()
                | AgenticForkSession()
                | AgenticGetSession()
                | AgenticAbortSession()
                | AgenticDeleteSession()
            ):
                value = _answer_session_effect(world, effect)
            case (
                AgenticSendMessage()
                | AgenticGetMessages()
                | AgenticNextEvent()
                | AgenticGetSessionStatus()
                | AgenticSupportsCapability()
            ):
                value = _answer_message_effect(world, effect)
            case _:
                return (yield Pass(effect, k))
        return (yield Resume(k, value))

    return answer_tmux_agentic


__all__ = [
    "tmux_handler",
]
