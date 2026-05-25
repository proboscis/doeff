"""Testing effect handler for deterministic agent tests."""


from dataclasses import dataclass, field
from datetime import datetime, timezone

from doeff_agents.adapters.base import AgentType
from doeff_agents.effects import (
    AgentSessionSnapshot,
    AttachAgentSessionEffect,
    CancelAgentSessionEffect,
    CaptureEffect,
    ClaudeLaunchEffect,
    CleanupAgentSessionEffect,
    GetAgentSessionEffect,
    LaunchEffect,
    LaunchTaskEffect,
    ListAgentSessionsEffect,
    MonitorEffect,
    Observation,
    ObserveAgentSessionEffect,
    SendEffect,
    SessionAlreadyExistsError,
    SessionHandle,
    SessionNotFoundError,
    StopEffect,
)
from doeff_agents.mcp_server import McpToolServer, RunToolFn
from doeff_agents.monitor import SessionStatus
from doeff_agents.session_store import AgentSessionRepository, InMemoryAgentSessionRepository

from .production import AgentHandler


@dataclass
class MockSessionScript:
    """Script for mock session behavior."""

    observations: list[tuple[SessionStatus, str]] = field(default_factory=list)
    _index: int = field(default=0, repr=False)

    def next_observation(self) -> tuple[SessionStatus, str]:
        """Get next observation from script."""
        if self._index >= len(self.observations):
            return (SessionStatus.DONE, "")
        obs = self.observations[self._index]
        self._index += 1
        return obs


@dataclass
class MockAgentState:
    """Serializable snapshot of mock handler state."""

    scripts: dict[str, MockSessionScript] = field(default_factory=dict)
    handles: dict[str, SessionHandle] = field(default_factory=dict)
    statuses: dict[str, SessionStatus] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    sends: list[tuple[str, str]] = field(default_factory=list)
    next_pane_id: int = 0


class MockAgentHandler(AgentHandler):
    """Mock handler for testing without tmux."""

    def __init__(self, session_repository: AgentSessionRepository | None = None) -> None:
        self._sessions: dict[str, MockSessionScript] = {}
        self._handles: dict[str, SessionHandle] = {}
        self._statuses: dict[str, SessionStatus] = {}
        self._outputs: dict[str, str] = {}
        self._sends: list[tuple[str, str]] = []
        self._next_pane_id: int = 0
        self._mcp_servers: dict[str, McpToolServer] = {}
        self._session_repository = (
            session_repository or InMemoryAgentSessionRepository()
        )

    def configure_session(
        self,
        session_name: str,
        script: MockSessionScript | None = None,
        initial_output: str = "",
    ) -> None:
        """Pre-configure a session for testing."""
        if script:
            self._sessions[session_name] = script
        self._outputs[session_name] = initial_output
        self._statuses[session_name] = SessionStatus.BOOTING

    def handle_launch(
        self,
        effect: LaunchEffect,
        run_tool: RunToolFn | None = None,
    ) -> SessionHandle:
        """Create mock session, optionally starting MCP server."""
        if effect.session_name in self._handles:
            raise SessionAlreadyExistsError(f"Session {effect.session_name} already exists")

        # Start MCP server if tools are provided (same as TmuxAgentHandler)
        if effect.mcp_tools and run_tool is not None:
            import json
            server = McpToolServer(tools=effect.mcp_tools, run_tool=run_tool)
            server.start()
            self._mcp_servers[effect.session_name] = server
            mcp_json_path = effect.work_dir / ".mcp.json"
            mcp_json_path.write_text(json.dumps({
                "mcpServers": {effect.mcp_server_name: {"type": "sse", "url": server.url}},
            }, indent=2))

        pane_id = f"%mock{self._next_pane_id}"
        self._next_pane_id += 1

        handle = SessionHandle(
            session_name=effect.session_name,
            pane_id=pane_id,
            agent_type=effect.agent_type,
            work_dir=effect.work_dir,
        )
        self._handles[effect.session_name] = handle
        self._statuses[effect.session_name] = SessionStatus.BOOTING
        self._outputs.setdefault(effect.session_name, "")
        self._record_snapshot("session_started", handle, SessionStatus.BOOTING)
        return handle

    def handle_launch_task(self, effect: LaunchTaskEffect) -> SessionHandle:
        """Create mock session for generic task launch."""
        raise NotImplementedError("LaunchTaskEffect is deprecated; use LaunchEffect directly")

    def handle_claude_launch(self, effect: ClaudeLaunchEffect) -> SessionHandle:
        """Create mock Claude session."""
        if effect.session_name in self._handles:
            raise SessionAlreadyExistsError(f"Session {effect.session_name} already exists")

        pane_id = f"%mock{self._next_pane_id}"
        self._next_pane_id += 1

        handle = SessionHandle(
            session_name=effect.session_name,
            pane_id=pane_id,
            agent_type=AgentType.CLAUDE,
            work_dir=effect.work_dir,
        )
        self._handles[effect.session_name] = handle
        self._statuses[effect.session_name] = SessionStatus.BOOTING
        self._outputs.setdefault(effect.session_name, "")
        self._record_snapshot("session_started", handle, SessionStatus.BOOTING)
        return handle

    def handle_monitor(self, effect: MonitorEffect) -> Observation:
        """Return next observation from script."""
        session_name = effect.handle.session_name

        if session_name not in self._handles:
            return Observation(status=SessionStatus.EXITED)

        script = self._sessions.get(session_name)
        if script:
            status, output = script.next_observation()
            self._statuses[session_name] = status
            self._outputs[session_name] = output
            observation = Observation(
                status=status,
                output_changed=True,
                output_snippet=output[-500:] if output else None,
            )
            self._record_snapshot(
                "session_observed",
                self._handles[session_name],
                status,
                output_snippet=observation.output_snippet,
            )
            return observation

        observation = Observation(
            status=self._statuses.get(session_name, SessionStatus.RUNNING),
            output_changed=False,
        )
        self._record_snapshot(
            "session_observed",
            self._handles[session_name],
            observation.status,
        )
        return observation

    def handle_capture(self, effect: CaptureEffect) -> str:
        """Return captured output."""
        session_name = effect.handle.session_name
        if session_name not in self._handles:
            raise SessionNotFoundError(f"Session {session_name} does not exist")
        output = self._outputs.get(session_name, "")
        self._record_snapshot(
            "session_captured",
            effect.handle,
            self._statuses.get(session_name, SessionStatus.RUNNING),
            output_snippet=output[-500:] if output else None,
        )
        return output

    def handle_send(self, effect: SendEffect) -> None:
        """Record sent message."""
        session_name = effect.handle.session_name
        if session_name not in self._handles:
            raise SessionNotFoundError(f"Session {session_name} does not exist")
        self._sends.append((session_name, effect.message))

    def handle_stop(self, effect: StopEffect) -> None:
        """Mark session as stopped and shut down MCP server (if any)."""
        session_name = effect.handle.session_name
        server = self._mcp_servers.pop(session_name, None)
        if server is not None:
            server.shutdown()
        if session_name in self._handles:
            self._statuses[session_name] = SessionStatus.STOPPED
            self._record_snapshot("session_stopped", effect.handle, SessionStatus.STOPPED)

    def handle_get_session(
        self,
        effect: GetAgentSessionEffect,
    ) -> AgentSessionSnapshot | None:
        """Return persisted mock session state."""
        return self._session_repository.get_session(effect.session_id)

    def handle_list_sessions(
        self,
        effect: ListAgentSessionsEffect,
    ) -> tuple[AgentSessionSnapshot, ...]:
        """Return persisted mock session states."""
        return self._session_repository.list_sessions(effect.query)

    def handle_observe_session(
        self,
        effect: ObserveAgentSessionEffect,
    ) -> AgentSessionSnapshot:
        """Observe a mock session by id."""
        snapshot = self._require_snapshot(effect.session_id)
        self.handle_monitor(MonitorEffect(handle=snapshot.to_handle()))
        updated = self._session_repository.get_session(effect.session_id)
        if updated is None:
            raise SessionNotFoundError(f"Session {effect.session_id} is not registered")
        return updated

    def handle_attach_session(self, effect: AttachAgentSessionEffect) -> None:
        """Mock attach is a no-op after validating the session exists."""
        self._require_snapshot(effect.session_id)

    def handle_cancel_session(
        self,
        effect: CancelAgentSessionEffect,
    ) -> AgentSessionSnapshot:
        """Cancel a mock session by id."""
        snapshot = self._require_snapshot(effect.session_id)
        self.handle_stop(StopEffect(handle=snapshot.to_handle()))
        updated = self._session_repository.get_session(effect.session_id)
        if updated is None:
            raise SessionNotFoundError(f"Session {effect.session_id} is not registered")
        return updated

    def handle_cleanup_session(
        self,
        effect: CleanupAgentSessionEffect,
    ) -> AgentSessionSnapshot:
        """Clean up a mock session by id."""
        snapshot = self._require_snapshot(effect.session_id)
        self._handles.pop(snapshot.session_name, None)
        self._statuses[snapshot.session_name] = SessionStatus.STOPPED
        now = datetime.now(timezone.utc)
        cleaned = snapshot.with_update(
            status=SessionStatus.STOPPED,
            cleaned_at=now,
            last_observed_at=now,
        )
        return self._session_repository.record_snapshot(
            "session_cleaned",
            cleaned,
        )

    def _require_snapshot(self, session_id: str) -> AgentSessionSnapshot:
        snapshot = self._session_repository.get_session(session_id)
        if snapshot is None:
            raise SessionNotFoundError(f"Session {session_id} is not registered")
        return snapshot

    def _record_snapshot(
        self,
        event_type: str,
        handle: SessionHandle,
        status: SessionStatus,
        *,
        output_snippet: str | None = None,
    ) -> AgentSessionSnapshot:
        now = datetime.now(timezone.utc)
        previous = self._session_repository.get_session(handle.session_id)
        finished_at = previous.finished_at if previous is not None else None
        if finished_at is None and status in (
            SessionStatus.DONE,
            SessionStatus.FAILED,
            SessionStatus.EXITED,
            SessionStatus.STOPPED,
        ):
            finished_at = now
        snapshot = AgentSessionSnapshot.from_handle(
            handle,
            status=status,
            last_observed_at=now,
            finished_at=finished_at,
            cleaned_at=previous.cleaned_at if previous is not None else None,
            pr_url=previous.pr_url if previous is not None else None,
            output_snippet=(
                output_snippet
                if output_snippet is not None
                else previous.output_snippet
                if previous is not None
                else None
            ),
        )
        return self._session_repository.record_snapshot(event_type, snapshot)

    @property
    def sent_messages(self) -> list[tuple[str, str]]:
        """Get all sent messages as (session_name, message) tuples."""
        return list(self._sends)

    def snapshot(self) -> MockAgentState:
        """Return a copyable state snapshot for compatibility/debugging."""
        return MockAgentState(
            scripts=dict(self._sessions),
            handles=dict(self._handles),
            statuses=dict(self._statuses),
            outputs=dict(self._outputs),
            sends=list(self._sends),
            next_pane_id=self._next_pane_id,
        )


__all__ = [
    "MockAgentHandler",
    "MockAgentState",
    "MockSessionScript",
]
