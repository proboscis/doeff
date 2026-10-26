# The cleanup scene: Step 2 of docs/19-agent-tutorial.md (review_safe.py). session_scope launches the session and
# stops it on the way out of the with block, however the block is left. Three ways out, each on the scripted
# terminal of stand_ins.py: the review finishes; a timeout (2 s against a 5 s review) returns early from inside the
# block; an exception is raised inside the block. Prints how the block was left and what the terminal killed.
import time
from pathlib import Path

from stand_ins import ScriptedTerminal

# The old (procedural) chapter-19 video measured these session procedures; they are internal to doeff-agents now
# (card ki-4b53dcee28f3) — the rewritten video measures the effects instead.
from doeff_agents import AgentType
from doeff_agents._session import capture_output, monitor_session, session_scope
from doeff_agents.adapters.base import LaunchConfig


def review_file(terminal: ScriptedTerminal, file_path: str, timeout: float, fail: bool = False) -> dict:
    config = LaunchConfig(agent_type=AgentType.CLAUDE, work_dir=Path.cwd(), prompt=f"Please review {file_path}.")
    with session_scope(f"review-{Path(file_path).stem}", config, backend=terminal) as session:
        start = time.time()
        while not session.is_terminal:
            if time.time() - start > timeout:
                return {"success": False, "error": "Timeout"}
            monitor_session(session)
            if fail:
                raise ValueError("bad review prompt")
            time.sleep(1)
        output = capture_output(session, lines=200)
        return {"ended": session.status.value, "review": output.splitlines()[2].strip()}


cases = [("finishes", "src/utils.py", 1.5, 120, False), ("times out", "src/api.py", 5.0, 2, False), ("raises", "src/cli.py", 5.0, 120, False)]
for label, path, work, timeout, _ in cases:
    terminal = ScriptedTerminal(work={path: work}, review={path: ["1. ok."]})
    try:
        result = review_file(terminal, path, timeout, fail=label == "raises")
        print(f"{label:9} -> returned {result}")
    except ValueError as error:
        print(f"{label:9} -> raised ValueError({str(error)!r})")
    print(f"{'':9}    stop_session ran: terminal killed {terminal.killed}, panes left {terminal.list_sessions()}")
