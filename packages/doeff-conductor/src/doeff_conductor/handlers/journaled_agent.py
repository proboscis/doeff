"""Journal-backed AgentEffect handler wrapper."""


from collections.abc import Callable
from pathlib import Path

from doeff_conductor.effects.agent import AgentEffect
from doeff_conductor.journal import AgentJournal, AgentReplaySession


class JournaledAgentHandler:
    """Replay cached L3 agent artifacts before delegating to a real handler."""

    def __init__(
        self,
        delegate: Callable[[AgentEffect], object],
        *,
        state_dir: str | Path | None = None,
        run_id: str | None = None,
    ) -> None:
        self.delegate = delegate
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self.run_id = run_id
        self._sessions: dict[str, AgentReplaySession] = {}

    def handle_agent(self, effect: AgentEffect) -> object:
        run_id = self.run_id or effect.task.run_id
        session = self._sessions.get(run_id)
        if session is None:
            session = AgentReplaySession(
                AgentJournal.for_run(
                    run_id,
                    state_dir=self.state_dir,
                )
            )
            self._sessions[run_id] = session
        return session.run_or_replay(effect, self.delegate)
