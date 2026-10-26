"""Drive a terminal (TUI) agent session with effects and test it with the mock handler.

The terminal handler (``agent_effectful_handler`` — tmux) answers ``Monitor``
(the session's status), ``Capture`` (the screen text), ``Send`` and ``Stop``.
The program watches the session until it is done, waiting between observations
with doeff-time's ``Delay`` (the time handler answers it), and returns the last
screen text. The test installs ``mock_agent_handlers(sessions=…)``: each
``MockSessionScript`` names a session and the observations its ``Monitor``
effects return in turn, so the program runs without tmux or an agent.

Run: uv run python packages/doeff-agents/examples/02_terminal_session_with_mock.py
"""

from pathlib import Path

from doeff_time import Delay

from doeff import do, run, with_handlers
from doeff_agents import (
    AgentType,
    Capture,
    Launch,
    MockSessionScript,
    Monitor,
    Send,
    SessionStatus,
    Stop,
    mock_agent_handlers,
)

TERMINAL = (SessionStatus.DONE, SessionStatus.FAILED, SessionStatus.EXITED, SessionStatus.STOPPED)


@do
def fix_and_report(name: str, work_dir: Path):
    """Ask the agent for a fix, answer its question, and return the final status and screen text."""
    handle = yield Launch(name, agent_type=AgentType.CLAUDE, work_dir=str(work_dir), prompt="Fix the failing test.")
    try:
        observation = yield Monitor(handle)
        while observation.status not in TERMINAL:
            if observation.status is SessionStatus.BLOCKED:
                yield Send(handle, "Yes, go ahead.")
            yield Delay(1.0)
            observation = yield Monitor(handle)
        screen = yield Capture(handle, lines=20)
        return observation.status, screen
    finally:
        yield Stop(handle)


def main() -> None:
    """Run the program under the mock handler with a scripted session and check its answer."""
    script = MockSessionScript(
        session_name="fix-1",
        observations=[
            (SessionStatus.RUNNING, "reading the test"),
            (SessionStatus.BLOCKED, "Apply the fix? (y/n)"),
            (SessionStatus.DONE, "fixed: 1 test passes"),
        ],
    )
    status, screen = run(with_handlers(list(mock_agent_handlers(sessions=(script,))), fix_and_report("fix-1", Path.cwd())))
    print(status, screen)
    assert status is SessionStatus.DONE, status
    assert screen == "fixed: 1 test passes", screen


if __name__ == "__main__":
    main()
