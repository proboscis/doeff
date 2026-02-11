"""Mock handlers for doeff-agentic effects used in tests and examples."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doeff import Resume
from doeff_agentic.effects import (
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
from doeff_agentic.exceptions import (
    AgenticDuplicateNameError,
    AgenticEnvironmentInUseError,
    AgenticEnvironmentNotFoundError,
    AgenticSessionNotFoundError,
    AgenticSessionNotRunningError,
)
from doeff_agentic.types import (
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


@dataclass
class MockAgenticState:
    """State snapshot for the mock handler."""

    workflow: AgenticWorkflowHandle | None
    environments: dict[str, AgenticEnvironmentHandle]
    sessions: dict[str, AgenticSessionHandle]
    session_by_id: dict[str, str]
    messages: dict[str, list[AgenticMessage]]


class MockAgenticHandler:
    """In-memory mock implementation of the agentic effect handlers."""

    SUPPORTED_CAPABILITIES = frozenset({"fork", "events", "worktree"})

    def __init__(
        self,
        workflow_id: str = "mock-workflow",
        workflow_name: str | None = "mock-workflow",
        working_dir: str | None = None,
    ) -> None:
        self._workflow: AgenticWorkflowHandle | None = None
        self._workflow_id = workflow_id
        self._workflow_name = workflow_name
        self._working_dir = Path(working_dir) if working_dir else Path.cwd()

        self._environments: dict[str, AgenticEnvironmentHandle] = {}
        self._sessions: dict[str, AgenticSessionHandle] = {}
        self._session_by_id: dict[str, str] = {}
        self._messages: dict[str, list[AgenticMessage]] = {}

        self._env_counter = 0
        self._session_counter = 0
        self._message_counter = 0

    def snapshot(self) -> MockAgenticState:
        """Return a copyable snapshot of the mock state."""
        return MockAgenticState(
            workflow=self._workflow,
            environments=dict(self._environments),
            sessions=dict(self._sessions),
            session_by_id=dict(self._session_by_id),
            messages={k: list(v) for k, v in self._messages.items()},
        )

    # ------------------------------------------------------------------
    # Workflow effects
    # ------------------------------------------------------------------

    def handle_create_workflow(self, effect: AgenticCreateWorkflow) -> AgenticWorkflowHandle:
        workflow_id = self._new_workflow_id(effect.name)
        self._workflow = AgenticWorkflowHandle(
            id=workflow_id,
            name=effect.name,
            status=AgenticWorkflowStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            metadata=effect.metadata,
        )
        return self._workflow

    def handle_get_workflow(self, effect: AgenticGetWorkflow) -> AgenticWorkflowHandle:
        self._ensure_workflow()
        assert self._workflow is not None
        return self._workflow

    # ------------------------------------------------------------------
    # Environment effects
    # ------------------------------------------------------------------

    def handle_create_environment(self, effect: AgenticCreateEnvironment) -> AgenticEnvironmentHandle:
        self._ensure_workflow()

        env_id = self._new_environment_id(effect.name)

        if effect.env_type == AgenticEnvironmentType.SHARED:
            working_dir = effect.working_dir or str(self._working_dir)
        elif effect.env_type == AgenticEnvironmentType.WORKTREE:
            working_dir = str(self._working_dir / f"mock-worktree-{env_id}")
        elif effect.env_type == AgenticEnvironmentType.INHERITED:
            if not effect.source_environment_id:
                raise ValueError("inherited type requires source_environment_id")
            source = self._environments.get(effect.source_environment_id)
            if source is None:
                raise AgenticEnvironmentNotFoundError(effect.source_environment_id)
            working_dir = source.working_dir
        elif effect.env_type == AgenticEnvironmentType.COPY:
            if not effect.source_environment_id:
                raise ValueError("copy type requires source_environment_id")
            source = self._environments.get(effect.source_environment_id)
            if source is None:
                raise AgenticEnvironmentNotFoundError(effect.source_environment_id)
            working_dir = f"{source.working_dir}-copy-{env_id}"
        else:
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
        self._environments[env_id] = handle
        return handle

    def handle_get_environment(self, effect: AgenticGetEnvironment) -> AgenticEnvironmentHandle:
        self._ensure_workflow()
        handle = self._environments.get(effect.environment_id)
        if handle is None:
            raise AgenticEnvironmentNotFoundError(effect.environment_id)
        return handle

    def handle_delete_environment(self, effect: AgenticDeleteEnvironment) -> bool:
        self._ensure_workflow()
        handle = self._environments.get(effect.environment_id)
        if handle is None:
            raise AgenticEnvironmentNotFoundError(effect.environment_id)

        using_sessions = [
            session.name
            for session in self._sessions.values()
            if session.environment_id == effect.environment_id
        ]
        if using_sessions and not effect.force:
            raise AgenticEnvironmentInUseError(effect.environment_id, using_sessions)

        del self._environments[effect.environment_id]
        return True

    # ------------------------------------------------------------------
    # Session effects
    # ------------------------------------------------------------------

    def handle_create_session(self, effect: AgenticCreateSession) -> AgenticSessionHandle:
        self._ensure_workflow()
        assert self._workflow is not None

        if effect.name in self._sessions:
            raise AgenticDuplicateNameError(effect.name, self._workflow.id)

        if effect.environment_id:
            if effect.environment_id not in self._environments:
                raise AgenticEnvironmentNotFoundError(effect.environment_id)
            env_id = effect.environment_id
        else:
            env_id = self._new_environment_id("shared")
            self._environments[env_id] = AgenticEnvironmentHandle(
                id=env_id,
                env_type=AgenticEnvironmentType.SHARED,
                name="shared",
                working_dir=str(self._working_dir),
                created_at=datetime.now(timezone.utc),
            )

        session_id = self._new_session_id()
        handle = AgenticSessionHandle(
            id=session_id,
            name=effect.name,
            workflow_id=self._workflow.id,
            environment_id=env_id,
            status=AgenticSessionStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            title=effect.title or effect.name,
            agent=effect.agent,
            model=effect.model,
        )

        self._sessions[effect.name] = handle
        self._session_by_id[session_id] = effect.name
        self._messages[session_id] = []
        return handle

    def handle_fork_session(self, effect: AgenticForkSession) -> AgenticSessionHandle:
        self._ensure_workflow()
        assert self._workflow is not None

        if effect.name in self._sessions:
            raise AgenticDuplicateNameError(effect.name, self._workflow.id)

        source_name = self._session_by_id.get(effect.session_id)
        if source_name is None:
            raise AgenticSessionNotFoundError(effect.session_id)

        source = self._sessions[source_name]
        new_id = self._new_session_id(prefix="fork")
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

        self._sessions[effect.name] = forked
        self._session_by_id[new_id] = effect.name
        self._messages[new_id] = list(self._messages.get(effect.session_id, []))
        return forked

    def handle_get_session(self, effect: AgenticGetSession) -> AgenticSessionHandle:
        self._ensure_workflow()

        if effect.name:
            session = self._sessions.get(effect.name)
            if session is None:
                raise AgenticSessionNotFoundError(effect.name, by_name=True)
            return session

        if effect.session_id:
            name = self._session_by_id.get(effect.session_id)
            if name is None:
                raise AgenticSessionNotFoundError(effect.session_id)
            return self._sessions[name]

        raise ValueError("Either session_id or name must be provided")

    def handle_abort_session(self, effect: AgenticAbortSession) -> None:
        session = self._get_session_by_id(effect.session_id)
        self._set_session_status(session, AgenticSessionStatus.ABORTED)

    def handle_delete_session(self, effect: AgenticDeleteSession) -> bool:
        name = self._session_by_id.pop(effect.session_id, None)
        if name is None:
            raise AgenticSessionNotFoundError(effect.session_id)

        self._sessions.pop(name, None)
        self._messages.pop(effect.session_id, None)
        return True

    # ------------------------------------------------------------------
    # Message effects
    # ------------------------------------------------------------------

    def handle_send_message(self, effect: AgenticSendMessage) -> AgenticMessageHandle:
        session = self._get_session_by_id(effect.session_id)
        if session.status in (
            AgenticSessionStatus.DONE,
            AgenticSessionStatus.ERROR,
            AgenticSessionStatus.ABORTED,
        ):
            raise AgenticSessionNotRunningError(effect.session_id, session.status.value)

        user_message = AgenticMessage(
            id=self._new_message_id(),
            session_id=effect.session_id,
            role="user",
            content=effect.content,
            created_at=datetime.now(timezone.utc),
        )
        self._messages[effect.session_id].append(user_message)

        if effect.wait:
            assistant_message = AgenticMessage(
                id=self._new_message_id(),
                session_id=effect.session_id,
                role="assistant",
                content=f"Mock response to: {effect.content[:80]}",
                created_at=datetime.now(timezone.utc),
            )
            self._messages[effect.session_id].append(assistant_message)
            self._set_session_status(session, AgenticSessionStatus.DONE)
        else:
            self._set_session_status(session, AgenticSessionStatus.RUNNING)

        return AgenticMessageHandle(
            id=user_message.id,
            session_id=effect.session_id,
            role="user",
            created_at=user_message.created_at,
        )

    def handle_get_messages(self, effect: AgenticGetMessages) -> list[AgenticMessage]:
        messages = list(self._messages.get(effect.session_id, []))
        if effect.limit is not None:
            messages = messages[-effect.limit :]
        return messages

    # ------------------------------------------------------------------
    # Event effects
    # ------------------------------------------------------------------

    def handle_next_event(self, effect: AgenticNextEvent) -> AgenticEvent | AgenticEndOfEvents:
        session = self._get_session_by_id(effect.session_id)

        if effect.timeout is not None and effect.timeout <= 0:
            return AgenticEndOfEvents(reason="timeout", final_status=session.status)

        if session.status.is_terminal():
            return AgenticEndOfEvents(
                reason=f"session_{session.status.value}",
                final_status=session.status,
            )

        self._set_session_status(session, AgenticSessionStatus.RUNNING)
        return AgenticEvent(
            event_type="mock.tick",
            session_id=effect.session_id,
            data={"timestamp_ns": time.time_ns()},
            timestamp=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Status effects
    # ------------------------------------------------------------------

    def handle_get_session_status(self, effect: AgenticGetSessionStatus) -> AgenticSessionStatus:
        session = self._get_session_by_id(effect.session_id)
        return session.status

    def handle_supports_capability(self, effect: AgenticSupportsCapability) -> bool:
        return effect.capability in self.SUPPORTED_CAPABILITIES

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_workflow(self) -> None:
        if self._workflow is None:
            self._workflow = AgenticWorkflowHandle(
                id=self._new_workflow_id(self._workflow_name),
                name=self._workflow_name,
                status=AgenticWorkflowStatus.RUNNING,
                created_at=datetime.now(timezone.utc),
                metadata=None,
            )

    def _get_session_by_id(self, session_id: str) -> AgenticSessionHandle:
        self._ensure_workflow()
        name = self._session_by_id.get(session_id)
        if name is None:
            raise AgenticSessionNotFoundError(session_id)
        return self._sessions[name]

    def _set_session_status(
        self,
        session: AgenticSessionHandle,
        status: AgenticSessionStatus,
    ) -> None:
        updated = AgenticSessionHandle(
            id=session.id,
            name=session.name,
            workflow_id=session.workflow_id,
            environment_id=session.environment_id,
            status=status,
            created_at=session.created_at,
            title=session.title,
            agent=session.agent,
            model=session.model,
        )
        self._sessions[session.name] = updated

    def _new_workflow_id(self, name: str | None) -> str:
        if self._workflow is not None:
            return self._workflow.id
        prefix = (name or self._workflow_id).replace(" ", "-").lower()
        return f"{prefix[:10]}-{int(time.time())}"

    def _new_environment_id(self, name: str | None) -> str:
        self._env_counter += 1
        stem = (name or "env").replace(" ", "-").lower()
        return f"env-{stem[:8]}-{self._env_counter}"

    def _new_session_id(self, prefix: str = "sess") -> str:
        self._session_counter += 1
        return f"{prefix}_{self._session_counter:04d}"

    def _new_message_id(self) -> str:
        self._message_counter += 1
        return f"msg_{self._message_counter:06d}"


def _as_protocol_handler(handler_fn):
    """Adapt an effect->value callable to doeff's protocol handler."""

    def _wrapped(effect: Any, k):
        return (yield Resume(k, handler_fn(effect)))

    return _wrapped


def mock_handlers(
    handler: MockAgenticHandler | None = None,
    *,
    workflow_id: str = "mock-workflow",
    workflow_name: str | None = "mock-workflow",
    working_dir: str | None = None,
) -> dict[type, Any]:
    """Create typed VM handler map for tests using in-memory state."""
    impl = handler or MockAgenticHandler(
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        working_dir=working_dir,
    )

    return {
        AgenticCreateWorkflow: _as_protocol_handler(impl.handle_create_workflow),
        AgenticGetWorkflow: _as_protocol_handler(impl.handle_get_workflow),
        AgenticCreateEnvironment: _as_protocol_handler(impl.handle_create_environment),
        AgenticGetEnvironment: _as_protocol_handler(impl.handle_get_environment),
        AgenticDeleteEnvironment: _as_protocol_handler(impl.handle_delete_environment),
        AgenticCreateSession: _as_protocol_handler(impl.handle_create_session),
        AgenticForkSession: _as_protocol_handler(impl.handle_fork_session),
        AgenticGetSession: _as_protocol_handler(impl.handle_get_session),
        AgenticAbortSession: _as_protocol_handler(impl.handle_abort_session),
        AgenticDeleteSession: _as_protocol_handler(impl.handle_delete_session),
        AgenticSendMessage: _as_protocol_handler(impl.handle_send_message),
        AgenticGetMessages: _as_protocol_handler(impl.handle_get_messages),
        AgenticNextEvent: _as_protocol_handler(impl.handle_next_event),
        AgenticGetSessionStatus: _as_protocol_handler(impl.handle_get_session_status),
        AgenticSupportsCapability: _as_protocol_handler(impl.handle_supports_capability),
    }


__all__ = [
    "MockAgenticHandler",
    "MockAgenticState",
    "mock_handlers",
]
