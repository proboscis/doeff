"""Testing effect handler for deterministic agent tests."""


from dataclasses import dataclass, field

from doeff_agents.effects import (
    CaptureEffect,
    LaunchEffect,
    MonitorEffect,
    Observation,
    SendEffect,
    SessionAlreadyExistsError,
    SessionHandle,
    SessionNotFoundError,
    SleepEffect,
    StopEffect,
)
from doeff_agents.monitor import SessionStatus

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
    sleep_calls: list[float] = field(default_factory=list)
    next_pane_id: int = 0


class MockAgentHandler(AgentHandler):
    """Mock handler for testing without tmux."""

    def __init__(self) -> None:
        self._sessions: dict[str, MockSessionScript] = {}
        self._handles: dict[str, SessionHandle] = {}
        self._statuses: dict[str, SessionStatus] = {}
        self._outputs: dict[str, str] = {}
        self._sends: list[tuple[str, str]] = []
        self._sleep_calls: list[float] = []
        self._next_pane_id: int = 0

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

    def handle_launch(self, effect: LaunchEffect) -> SessionHandle:
        """Create mock session."""
        if effect.session_name in self._handles:
            raise SessionAlreadyExistsError(f"Session {effect.session_name} already exists")

        pane_id = f"%mock{self._next_pane_id}"
        self._next_pane_id += 1

        handle = SessionHandle(
            session_name=effect.session_name,
            pane_id=pane_id,
            agent_type=effect.config.agent_type,
            work_dir=effect.config.work_dir,
        )
        self._handles[effect.session_name] = handle
        self._statuses[effect.session_name] = SessionStatus.BOOTING
        self._outputs.setdefault(effect.session_name, "")
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
            return Observation(
                status=status,
                output_changed=True,
                output_snippet=output[-500:] if output else None,
            )

        return Observation(
            status=self._statuses.get(session_name, SessionStatus.RUNNING),
            output_changed=False,
        )

    def handle_capture(self, effect: CaptureEffect) -> str:
        """Return captured output."""
        session_name = effect.handle.session_name
        if session_name not in self._handles:
            raise SessionNotFoundError(f"Session {session_name} does not exist")
        return self._outputs.get(session_name, "")

    def handle_send(self, effect: SendEffect) -> None:
        """Record sent message."""
        session_name = effect.handle.session_name
        if session_name not in self._handles:
            raise SessionNotFoundError(f"Session {session_name} does not exist")
        self._sends.append((session_name, effect.message))

    def handle_stop(self, effect: StopEffect) -> None:
        """Mark session as stopped."""
        session_name = effect.handle.session_name
        if session_name in self._handles:
            self._statuses[session_name] = SessionStatus.STOPPED

    def handle_sleep(self, effect: SleepEffect) -> None:
        """Record sleep call (no actual delay)."""
        self._sleep_calls.append(effect.seconds)

    @property
    def sent_messages(self) -> list[tuple[str, str]]:
        """Get all sent messages as (session_name, message) tuples."""
        return list(self._sends)

    @property
    def total_sleep_time(self) -> float:
        """Get total sleep time requested."""
        return sum(self._sleep_calls)

    def snapshot(self) -> MockAgentState:
        """Return a copyable state snapshot for compatibility/debugging."""
        return MockAgentState(
            scripts=dict(self._sessions),
            handles=dict(self._handles),
            statuses=dict(self._statuses),
            outputs=dict(self._outputs),
            sends=list(self._sends),
            sleep_calls=list(self._sleep_calls),
            next_pane_id=self._next_pane_id,
        )


__all__ = [
    "MockAgentHandler",
    "MockAgentState",
    "MockSessionScript",
]
