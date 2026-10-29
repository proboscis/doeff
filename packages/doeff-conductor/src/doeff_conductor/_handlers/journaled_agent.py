"""Journal-backed AgentEffect handler: replay cached L3 agent artifacts before running agents."""

from pathlib import Path

from doeff import K, Resume, handler
from doeff.program import ProgramHandler
from doeff_conductor._effects.agent import AgentEffect
from doeff_conductor._journal import AgentJournal, AgentReplaySession, _ReplayedAgent

__all__ = ["journaled_agent_handler"]


def journaled_agent_handler(
    *,
    state_dir: str | Path | None = None,
    run_id: str | None = None,
) -> ProgramHandler:
    """Build the handler that replays an agent effect's journaled artifact, or runs and journals it.

    A resumed run answers the cached prefix of agent effects from the run's agent journal. Any
    other agent effect is performed outward — the handler is installed inside the handler that
    runs agents (``agent_handler``, or a test's) — and its validated artifact, or the open gate
    its attempt exhaustion or deadline parks as, is journaled.

    Args:
        state_dir: The conductor state directory. The XDG default if not provided.
        run_id: The run whose journal is used. The agent task's run id if not provided.
    """
    journal_dir: Path | None = Path(state_dir) if state_dir is not None else None
    sessions: dict[str, AgentReplaySession] = {}

    def session_for(effect: AgentEffect) -> AgentReplaySession:
        """The replay cursor of the run the effect belongs to, opened on its first agent effect."""
        active_run_id: str = run_id or effect.task.run_id
        session = sessions.get(active_run_id)
        if session is None:
            session = AgentReplaySession(AgentJournal.for_run(active_run_id, state_dir=journal_dir))
            sessions[active_run_id] = session
        return session

    @handler
    def replay_or_run_agent(effect: AgentEffect, k: K):
        """Answer an agent effect from the journal, or run it outward and journal the outcome."""
        session: AgentReplaySession = session_for(effect)
        step = session.begin(effect)
        if isinstance(step, _ReplayedAgent):
            return (yield Resume(k, step.result_artifact))
        try:
            result = yield effect
        except Exception as error:
            session.failed(effect, step, error)
            raise
        return (yield Resume(k, session.succeeded(effect, step, result)))

    return replay_or_run_agent
