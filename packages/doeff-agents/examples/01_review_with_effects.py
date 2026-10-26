"""Review diffs with a coding agent by yielding doeff-agents effects (no process, no API).

The program launches an agent session (``Launch``), waits for the turn's result
inside the handler (``AwaitResult``), reads the result, and stops the session
(``Stop``) in a ``finally``. A second program reads the turn's events (``Events``)
and answers a failed turn with ``FollowUp``; a third runs reviews in parallel with
doeff's ``Spawn`` / ``Gather``. Nothing polls, sleeps or registers a callback.

The handlers are installed at one entry point: here the fake Claude runtime
(``fake_claude_agent_runtime_handlers``); for the real CLI, install
``claude_agent_runtime_handlers`` instead — the programs stay the same.

Run: uv run python packages/doeff-agents/examples/01_review_with_effects.py
"""

import tempfile
from pathlib import Path

from doeff_core_effects.scheduler import scheduled
from doeff_time import SimClock, sim_time_handler

from doeff import Gather, Spawn, do, run, with_handlers
from doeff_agents import (
    AgentToolUseEvent,
    AgentTurnCompleted,
    AgentTurnFailed,
    AgentType,
    AwaitResult,
    Events,
    FakeReply,
    FollowUp,
    Launch,
    Stop,
    fake_claude_agent_runtime_handlers,
)


@do
def review(name: str, work_dir: Path, diff: str):
    """Have the agent review one diff and return the review text."""
    handle = yield Launch(name, agent_type=AgentType.CLAUDE, work_dir=str(work_dir), prompt=f"Review this diff:\n{diff}")
    try:
        outcome = yield AwaitResult(handle, timeout_seconds=600.0)
        return outcome.result
    finally:
        yield Stop(handle)


@do
def review_with_retry(name: str, work_dir: Path, diff: str):
    """Read the turn's events and ask once more when the turn failed; return (text, tool calls, retried)."""
    handle = yield Launch(name, agent_type=AgentType.CLAUDE, work_dir=str(work_dir), prompt=f"Review this diff:\n{diff}")
    try:
        seq, tool_calls, retried = -1, 0, False
        while True:
            page = yield Events(handle, after_seq=seq, wait_seconds=30.0)
            seq = page.next_seq
            tool_calls += sum(len(event.tool_calls) for event in page.events if isinstance(event, AgentToolUseEvent))
            match page.end:
                case None:
                    continue
                case AgentTurnFailed() if not retried:
                    retried = True
                    yield FollowUp(handle, "The previous turn failed. Please review again.")
                case AgentTurnCompleted(result_text=text):
                    return (text, tool_calls, retried)
                case other:
                    raise RuntimeError(f"review {name} ended without a review: {other!r}")
    finally:
        yield Stop(handle)


@do
def review_all(work_dir: Path, diffs: tuple[tuple[str, str], ...]):
    """Run one review per diff in parallel and return the reviews in the input order."""
    tasks = ()
    for name, diff in diffs:
        tasks += ((yield Spawn(review(name, work_dir, diff))),)
    return (yield Gather(*tasks))


def responder(text: str, memory: tuple[str, ...]) -> FakeReply:
    """Script the fake agent: the first request containing ``fail-once`` fails, every other one is reviewed."""
    if "fail-once" in text and not memory:
        return FakeReply("", fail="the fake agent failed")
    return FakeReply(f"reviewed: {text.splitlines()[-1]}")


def handlers() -> list:
    """The handlers installed around the programs (outer first): the clock, then the fake Claude runtime."""
    return [sim_time_handler(clock=SimClock()), *fake_claude_agent_runtime_handlers(responder=responder, env={}, settings={})]


def main() -> None:
    """Run the programs on the fake runtime and check their answers."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        parallel = run(scheduled(with_handlers(handlers(), review_all(work, (("a", "diff A"), ("b", "diff B"))))))
        print("parallel:", parallel)
        assert list(parallel) == ["reviewed: diff A", "reviewed: diff B"], parallel
        retried = run(scheduled(with_handlers(handlers(), review_with_retry("c", work, "fail-once diff C"))))
        print("retry:", retried)
        assert retried[0].startswith("reviewed: "), retried
        assert retried[2] is True, retried


if __name__ == "__main__":
    main()
