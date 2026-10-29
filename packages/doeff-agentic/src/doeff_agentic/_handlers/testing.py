"""Mock handler for doeff-agentic effects used in tests and examples.

``mock_handlers()`` returns a doeff handler that answers the agentic effects from
in-memory state. The state lives in the handler's closure: each call makes a fresh
world, and nothing is shared between two handlers.
"""


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
from doeff_agentic._exceptions import (
    AgenticDuplicateNameError,
    AgenticEnvironmentInUseError,
    AgenticEnvironmentNotFoundError,
    AgenticSessionNotFoundError,
    AgenticSessionNotRunningError,
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

_SUPPORTED_CAPABILITIES = frozenset({"fork", "events", "worktree"})


@dataclass
class _MockWorld:
    """The in-memory state one ``mock_handlers()`` handler keeps in its closure."""

    workflow_id: str
    workflow_name: str | None
    working_dir: Path
    workflow: AgenticWorkflowHandle | None = None
    environments: dict[str, AgenticEnvironmentHandle] = field(default_factory=dict)
    sessions: dict[str, AgenticSessionHandle] = field(default_factory=dict)
    session_by_id: dict[str, str] = field(default_factory=dict)
    messages: dict[str, tuple[AgenticMessage, ...]] = field(default_factory=dict)
    env_counter: int = 0
    session_counter: int = 0
    message_counter: int = 0


# ----------------------------------------------------------------------
# Ids and state helpers
# ----------------------------------------------------------------------


def _new_workflow_id(world: _MockWorld, name: str | None) -> str:
    if world.workflow is not None:
        return world.workflow.id
    prefix = (name or world.workflow_id).replace(" ", "-").lower()
    return f"{prefix[:10]}-{int(time.time())}"


def _new_environment_id(world: _MockWorld, name: str | None) -> str:
    world.env_counter += 1
    stem = (name or "env").replace(" ", "-").lower()
    return f"env-{stem[:8]}-{world.env_counter}"


def _new_session_id(world: _MockWorld, prefix: str = "sess") -> str:
    world.session_counter += 1
    return f"{prefix}_{world.session_counter:04d}"


def _new_message_id(world: _MockWorld) -> str:
    world.message_counter += 1
    return f"msg_{world.message_counter:06d}"


def _ensure_workflow(world: _MockWorld) -> AgenticWorkflowHandle:
    if world.workflow is None:
        world.workflow = AgenticWorkflowHandle(
            id=_new_workflow_id(world, world.workflow_name),
            name=world.workflow_name,
            status=AgenticWorkflowStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            metadata=None,
        )
    return world.workflow


def _session_by_id(world: _MockWorld, session_id: str) -> AgenticSessionHandle:
    _ensure_workflow(world)
    name = world.session_by_id.get(session_id)
    if name is None:
        raise AgenticSessionNotFoundError(session_id)
    return world.sessions[name]


def _set_session_status(
    world: _MockWorld, session: AgenticSessionHandle, status: AgenticSessionStatus
) -> None:
    world.sessions[session.name] = replace(session, status=status)


# ----------------------------------------------------------------------
# Answers (one per effect)
# ----------------------------------------------------------------------


def _create_workflow(world: _MockWorld, effect: AgenticCreateWorkflow) -> AgenticWorkflowHandle:
    world.workflow = AgenticWorkflowHandle(
        id=_new_workflow_id(world, effect.name),
        name=effect.name,
        status=AgenticWorkflowStatus.RUNNING,
        created_at=datetime.now(timezone.utc),
        metadata=effect.metadata,
    )
    return world.workflow


def _source_environment(
    world: _MockWorld, effect: AgenticCreateEnvironment, env_kind: str
) -> AgenticEnvironmentHandle:
    if not effect.source_environment_id:
        raise ValueError(f"{env_kind} type requires source_environment_id")
    source = world.environments.get(effect.source_environment_id)
    if source is None:
        raise AgenticEnvironmentNotFoundError(effect.source_environment_id)
    return source


def _create_environment(
    world: _MockWorld, effect: AgenticCreateEnvironment
) -> AgenticEnvironmentHandle:
    _ensure_workflow(world)
    env_id = _new_environment_id(world, effect.name)

    match effect.env_type:
        case AgenticEnvironmentType.SHARED:
            working_dir = effect.working_dir or str(world.working_dir)
        case AgenticEnvironmentType.WORKTREE:
            working_dir = str(world.working_dir / f"mock-worktree-{env_id}")
        case AgenticEnvironmentType.INHERITED:
            working_dir = _source_environment(world, effect, "inherited").working_dir
        case AgenticEnvironmentType.COPY:
            source = _source_environment(world, effect, "copy")
            working_dir = f"{source.working_dir}-copy-{env_id}"
        case _:
            raise ValueError(f"Unsupported environment type: {effect.env_type}")

    handle = AgenticEnvironmentHandle(
        id=env_id,
        env_type=effect.env_type,
        name=effect.name,
        working_dir=working_dir,
        created_at=datetime.now(timezone.utc),
        base_commit=effect.base_commit,
        source_environment_id=effect.source_environment_id,
    )
    world.environments[env_id] = handle
    return handle


def _get_environment(world: _MockWorld, effect: AgenticGetEnvironment) -> AgenticEnvironmentHandle:
    _ensure_workflow(world)
    handle = world.environments.get(effect.environment_id)
    if handle is None:
        raise AgenticEnvironmentNotFoundError(effect.environment_id)
    return handle


def _delete_environment(world: _MockWorld, effect: AgenticDeleteEnvironment) -> bool:
    _ensure_workflow(world)
    if effect.environment_id not in world.environments:
        raise AgenticEnvironmentNotFoundError(effect.environment_id)

    using_sessions = [
        session.name
        for session in world.sessions.values()
        if session.environment_id == effect.environment_id
    ]
    if using_sessions and not effect.force:
        raise AgenticEnvironmentInUseError(effect.environment_id, using_sessions)

    del world.environments[effect.environment_id]
    return True


def _create_session(world: _MockWorld, effect: AgenticCreateSession) -> AgenticSessionHandle:
    workflow = _ensure_workflow(world)

    if effect.name in world.sessions:
        raise AgenticDuplicateNameError(effect.name, workflow.id)

    if effect.environment_id:
        if effect.environment_id not in world.environments:
            raise AgenticEnvironmentNotFoundError(effect.environment_id)
        env_id = effect.environment_id
    else:
        env_id = _new_environment_id(world, "shared")
        world.environments[env_id] = AgenticEnvironmentHandle(
            id=env_id,
            env_type=AgenticEnvironmentType.SHARED,
            name="shared",
            working_dir=str(world.working_dir),
            created_at=datetime.now(timezone.utc),
        )

    session_id = _new_session_id(world)
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

    world.sessions[effect.name] = handle
    world.session_by_id[session_id] = effect.name
    world.messages[session_id] = ()
    return handle


def _fork_session(world: _MockWorld, effect: AgenticForkSession) -> AgenticSessionHandle:
    workflow = _ensure_workflow(world)

    if effect.name in world.sessions:
        raise AgenticDuplicateNameError(effect.name, workflow.id)

    source_name = world.session_by_id.get(effect.session_id)
    if source_name is None:
        raise AgenticSessionNotFoundError(effect.session_id)

    source = world.sessions[source_name]
    new_id = _new_session_id(world, prefix="fork")
    forked = AgenticSessionHandle(
        id=new_id,
        name=effect.name,
        workflow_id=source.workflow_id,
        environment_id=source.environment_id,
        status=AgenticSessionStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        title=effect.name,
        agent=source.agent,
        model=source.model,
    )

    world.sessions[effect.name] = forked
    world.session_by_id[new_id] = effect.name
    world.messages[new_id] = world.messages.get(effect.session_id, ())
    return forked


def _get_session(world: _MockWorld, effect: AgenticGetSession) -> AgenticSessionHandle:
    _ensure_workflow(world)

    if effect.name:
        session = world.sessions.get(effect.name)
        if session is None:
            raise AgenticSessionNotFoundError(effect.name, by_name=True)
        return session

    if effect.session_id:
        return _session_by_id(world, effect.session_id)

    raise ValueError("Either session_id or name must be provided")


def _abort_session(world: _MockWorld, effect: AgenticAbortSession) -> None:
    session = _session_by_id(world, effect.session_id)
    _set_session_status(world, session, AgenticSessionStatus.ABORTED)


def _delete_session(world: _MockWorld, effect: AgenticDeleteSession) -> bool:
    name = world.session_by_id.pop(effect.session_id, None)
    if name is None:
        raise AgenticSessionNotFoundError(effect.session_id)

    world.sessions.pop(name, None)
    world.messages.pop(effect.session_id, None)
    return True


def _send_message(world: _MockWorld, effect: AgenticSendMessage) -> AgenticMessageHandle:
    session = _session_by_id(world, effect.session_id)
    if session.status in (
        AgenticSessionStatus.DONE,
        AgenticSessionStatus.ERROR,
        AgenticSessionStatus.ABORTED,
    ):
        raise AgenticSessionNotRunningError(effect.session_id, session.status.value)

    user_message = AgenticMessage(
        id=_new_message_id(world),
        session_id=effect.session_id,
        role="user",
        content=effect.content,
        created_at=datetime.now(timezone.utc),
    )
    world.messages[effect.session_id] = (*world.messages[effect.session_id], user_message)

    if effect.wait:
        assistant_message = AgenticMessage(
            id=_new_message_id(world),
            session_id=effect.session_id,
            role="assistant",
            content=f"Mock response to: {effect.content[:80]}",
            created_at=datetime.now(timezone.utc),
        )
        world.messages[effect.session_id] = (
            *world.messages[effect.session_id],
            assistant_message,
        )
        _set_session_status(world, session, AgenticSessionStatus.DONE)
    else:
        _set_session_status(world, session, AgenticSessionStatus.RUNNING)

    return AgenticMessageHandle(
        id=user_message.id,
        session_id=effect.session_id,
        role="user",
        created_at=user_message.created_at,
    )


def _get_messages(world: _MockWorld, effect: AgenticGetMessages) -> list[AgenticMessage]:
    messages = list(world.messages.get(effect.session_id, ()))
    if effect.limit is not None:
        return messages[-effect.limit :]
    return messages


def _next_event(
    world: _MockWorld, effect: AgenticNextEvent
) -> AgenticEvent | AgenticEndOfEvents:
    session = _session_by_id(world, effect.session_id)

    if effect.timeout is not None and effect.timeout <= 0:
        return AgenticEndOfEvents(reason="timeout", final_status=session.status)

    if session.status.is_terminal():
        return AgenticEndOfEvents(
            reason=f"session_{session.status.value}",
            final_status=session.status,
        )

    _set_session_status(world, session, AgenticSessionStatus.RUNNING)
    return AgenticEvent(
        event_type="mock.tick",
        session_id=effect.session_id,
        data={"timestamp_ns": time.time_ns()},
        timestamp=datetime.now(timezone.utc),
    )


def _answer_workflow_effect(world: _MockWorld, effect: WorkflowEffect) -> object:
    """The mock's answer to a workflow or environment effect."""
    match effect:
        case AgenticCreateWorkflow():
            return _create_workflow(world, effect)
        case AgenticGetWorkflow():
            return _ensure_workflow(world)
        case AgenticCreateEnvironment():
            return _create_environment(world, effect)
        case AgenticGetEnvironment():
            return _get_environment(world, effect)
        case AgenticDeleteEnvironment():
            return _delete_environment(world, effect)


def _answer_session_effect(world: _MockWorld, effect: SessionEffect) -> object:
    """The mock's answer to a session lifecycle effect."""
    match effect:
        case AgenticCreateSession():
            return _create_session(world, effect)
        case AgenticForkSession():
            return _fork_session(world, effect)
        case AgenticGetSession():
            return _get_session(world, effect)
        case AgenticAbortSession():
            return _abort_session(world, effect)
        case AgenticDeleteSession():
            return _delete_session(world, effect)


def _answer_message_effect(world: _MockWorld, effect: MessageEffect) -> object:
    """The mock's answer to a message, event or status effect."""
    match effect:
        case AgenticSendMessage():
            return _send_message(world, effect)
        case AgenticGetMessages():
            return _get_messages(world, effect)
        case AgenticNextEvent():
            return _next_event(world, effect)
        case AgenticGetSessionStatus():
            return _session_by_id(world, effect.session_id).status
        case AgenticSupportsCapability():
            return effect.capability in _SUPPORTED_CAPABILITIES


def mock_handlers(
    *,
    workflow_id: str = "mock-workflow",
    workflow_name: str | None = "mock-workflow",
    working_dir: str | None = None,
) -> ProgramHandler:
    """Return a doeff handler that answers the agentic effects from in-memory state.

    The state (workflow, environments, sessions, messages) lives in the handler's
    closure, so each call starts from an empty world. Effects it does not own go
    to the handlers outside it.
    """
    world = _MockWorld(
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        working_dir=Path(working_dir) if working_dir else Path.cwd(),
    )

    @handler
    def answer_mock_agentic(effect: Effect, k: Any):
        """Answer an agentic effect from the in-memory world; pass every other effect outward."""
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

    return answer_mock_agentic


__all__ = [
    "mock_handlers",
]
