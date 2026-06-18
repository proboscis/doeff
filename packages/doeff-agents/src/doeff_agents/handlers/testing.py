"""Testing effect handler for deterministic agent tests."""


from dataclasses import dataclass, field
from datetime import datetime, timezone

from doeff import Effect, Pass, Resume, do
from doeff import handler as _install_raw_handler
from doeff_agents.adapters.base import AgentType
from doeff_agents.effects import (
    AgentEffect,
    AgentSessionLifecycle,
    AgentSessionSnapshot,
    AttachAgentSessionEffect,
    AwaitOutcome,
    AwaitResultEffect,
    AwaitStatus,
    CancelAgentSessionEffect,
    CaptureEffect,
    ClaudeLaunchEffect,
    CleanupAgentSessionEffect,
    FollowUpEffect,
    GetAgentSessionEffect,
    L2SessionHandle,
    LaunchEffect,
    LaunchSessionEffect,
    LaunchTaskEffect,
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
        self._agent_types: dict[str, AgentType] = {}
        self._work_dirs: dict[str, object] = {}
        self._lifecycles: dict[str, AgentSessionLifecycle] = {}
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

        self._next_pane_id += 1

        handle = SessionHandle(
            session_id=effect.session_name,
        )
        self._handles[effect.session_name] = handle
        self._statuses[effect.session_name] = SessionStatus.BOOTING
        self._outputs.setdefault(effect.session_name, "")
        self._agent_types[effect.session_name] = effect.agent_type
        self._work_dirs[effect.session_name] = effect.work_dir
        self._lifecycles[effect.session_name] = effect.lifecycle
        self._record_snapshot("session_started", handle, SessionStatus.BOOTING)
        return handle

    def handle_launch_task(self, effect: LaunchTaskEffect) -> SessionHandle:
        """Create mock session for generic task launch."""
        raise NotImplementedError("LaunchTaskEffect is deprecated; use LaunchEffect directly")

    def handle_claude_launch(self, effect: ClaudeLaunchEffect) -> SessionHandle:
        """Create mock Claude session."""
        if effect.session_name in self._handles:
            raise SessionAlreadyExistsError(f"Session {effect.session_name} already exists")

        self._next_pane_id += 1

        handle = SessionHandle(
            session_id=effect.session_name,
        )
        self._handles[effect.session_name] = handle
        self._statuses[effect.session_name] = SessionStatus.BOOTING
        self._outputs.setdefault(effect.session_name, "")
        self._agent_types[effect.session_name] = AgentType.CLAUDE
        self._work_dirs[effect.session_name] = effect.work_dir
        self._lifecycles[effect.session_name] = effect.lifecycle
        self._record_snapshot("session_started", handle, SessionStatus.BOOTING)
        return handle

    def handle_monitor(self, effect: MonitorEffect) -> Observation:
        """Return next observation from script."""
        session_name = effect.handle.session_id

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
        session_name = effect.handle.session_id
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
        session_name = effect.handle.session_id
        if session_name not in self._handles:
            raise SessionNotFoundError(f"Session {session_name} does not exist")
        self._sends.append((session_name, effect.message))

    def handle_stop(self, effect: StopEffect) -> None:
        """Mark session as stopped and shut down MCP server (if any)."""
        session_name = effect.handle.session_id
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
            backend_ref={
                "session_name": handle.session_id,
                "agent_type": self._agent_types.get(handle.session_id, AgentType.CUSTOM).value,
                "work_dir": str(self._work_dirs.get(handle.session_id, ".")),
            },
            lifecycle=self._lifecycles.get(handle.session_id),
            last_observed_at=now,
            finished_at=finished_at,
            cleaned_at=previous.cleaned_at if previous is not None else None,
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


@dataclass(frozen=True)
class ScenarioStep:
    """One scripted L2 await outcome for the scenario stub."""

    status: AwaitStatus
    payload: object | None = None
    validation_error: str | None = None
    continuable: bool = True

    @classmethod
    def success(cls, payload: object) -> "ScenarioStep":
        return cls(status=AwaitStatus.EXITED, payload=payload)

    @classmethod
    def invalid(cls, *, payload: object, validation_error: str) -> "ScenarioStep":
        return cls(
            status=AwaitStatus.EXITED,
            payload=payload,
            validation_error=validation_error,
        )

    @classmethod
    def terminal_invalid(cls, *, validation_error: str) -> "ScenarioStep":
        """A failure from a TERMINAL (supervisor-reaped) session.

        Models the agentd route: the supervisor spent the contract
        retries and cleaned the pane — no follow-up can reach it.
        """
        return cls(
            status=AwaitStatus.EXITED,
            validation_error=validation_error,
            continuable=False,
        )

    @classmethod
    def absent(cls) -> "ScenarioStep":
        return cls(status=AwaitStatus.EXITED)

    @classmethod
    def awaiting_input(cls, message: str) -> "ScenarioStep":
        return cls(status=AwaitStatus.AWAITING_INPUT, validation_error=message)

    @classmethod
    def timeout(cls) -> "ScenarioStep":
        return cls(status=AwaitStatus.TIMED_OUT)


class ScenarioAgentHandler(MockAgentHandler):
    """Scenario-driven C1 stub handler.

    Scripts are keyed by deterministic session id.  Each await consumes one
    step, so retries are explicit in the test data.
    """

    def __init__(
        self,
        *,
        scripts: dict[str, list[ScenarioStep]] | None = None,
    ) -> None:
        super().__init__()
        self._scenario_scripts: dict[str, list[ScenarioStep]] = {
            session_id: list(steps) for session_id, steps in (scripts or {}).items()
        }
        self._scenario_indices: dict[str, int] = {}
        self._launch_counts: dict[str, int] = {}
        self._follow_ups: dict[str, list[str]] = {}
        self.stopped_sessions: list[str] = []
        self.released_sessions: list[str] = []

    def wrap(self, program):
        """Wrap a Program with this scenario handler."""

        @do
        def handler(effect: Effect, k):
            if isinstance(effect, AgentEffect):
                return (yield Resume(k, self.handle_agent(effect)))
            if isinstance(effect, LaunchSessionEffect):
                return (yield Resume(k, self.handle_launch_session(effect)))
            if isinstance(effect, AwaitResultEffect):
                return (yield Resume(k, self.handle_await_result(effect)))
            if isinstance(effect, FollowUpEffect):
                return (yield Resume(k, self.handle_follow_up(effect)))
            if isinstance(effect, StopSessionEffect):
                self.handle_stop_session(effect)
                return (yield Resume(k, None))
            if isinstance(effect, ReleaseSessionEffect):
                self.handle_release_session(effect)
                return (yield Resume(k, None))
            yield Pass(effect, k)

        return _install_raw_handler(handler)(program)

    def handle_launch_session(
        self,
        effect: LaunchSessionEffect,
        run_tool: RunToolFn | None = None,
    ) -> L2SessionHandle:
        session_id = effect.spec.session_id
        if session_id not in self._handles:
            self._launch_counts[session_id] = self._launch_counts.get(session_id, 0) + 1
            self._handles[session_id] = L2SessionHandle(session_id=session_id)
        return L2SessionHandle(session_id=session_id)

    def handle_await_result(self, effect: AwaitResultEffect) -> AwaitOutcome:
        session_id = effect.handle.session_id
        script = self._scenario_scripts.get(session_id, [ScenarioStep.success({})])
        index = self._scenario_indices.get(session_id, 0)
        if index >= len(script):
            step = script[-1]
        else:
            step = script[index]
            self._scenario_indices[session_id] = index + 1
        return AwaitOutcome(
            status=step.status,
            result=step.payload,
            validation_error=step.validation_error,
            continuable=step.continuable,
        )

    def handle_follow_up(self, effect: FollowUpEffect) -> L2SessionHandle:
        self._follow_ups.setdefault(effect.handle.session_id, []).append(effect.message)
        return effect.handle

    def handle_stop_session(self, effect: StopSessionEffect) -> None:
        self.stopped_sessions.append(effect.handle.session_id)

    def handle_release_session(self, effect: ReleaseSessionEffect) -> None:
        self.released_sessions.append(effect.handle.session_id)

    def configure_script(self, session_id: str, steps: list[ScenarioStep]) -> None:
        """Replace a session's scripted outcomes (e.g. between park and resume)."""
        self._scenario_scripts[session_id] = list(steps)
        self._scenario_indices[session_id] = 0

    def launch_count(self, session_id: str) -> int:
        return self._launch_counts.get(session_id, 0)

    def follow_up_messages(self, session_id: str) -> list[str]:
        return list(self._follow_ups.get(session_id, []))


__all__ = [
    "MockAgentHandler",
    "MockAgentState",
    "MockSessionScript",
    "ScenarioAgentHandler",
    "ScenarioStep",
]
