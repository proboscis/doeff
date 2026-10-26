# The lifecycle scene: Step 1 of docs/19-agent-tutorial.md (review_basic.py), with the scripted terminal of
# stand_ins.py as the backend. launch_session -> monitor_session once a second until the session is terminal ->
# capture_output -> stop_session. Prints the status after each call, with the seconds since the launch.
import time
from pathlib import Path

from stand_ins import ScriptedTerminal

# The old (procedural) chapter-19 video measured these session procedures; they are internal to doeff-agents now
# (card ki-4b53dcee28f3) — the rewritten video measures the effects instead.
from doeff_agents import AgentType
from doeff_agents._session import capture_output, launch_session, monitor_session, stop_session
from doeff_agents.adapters.base import LaunchConfig

terminal = ScriptedTerminal(
    work={"src/utils.py": 2.5},
    review={"src/utils.py": [
        "1. parse_port() accepts 0; reject ports outside 1-65535.",
        "2. load_config() hides FileNotFoundError; name the missing file.",
        "3. retry() always sleeps 1 s; back off instead.",
    ]},
)
config = LaunchConfig(
    agent_type=AgentType.CLAUDE,
    work_dir=Path.cwd(),
    prompt="Review the code in src/utils.py and provide feedback on code quality, potential bugs, and improvements.",
)
start = time.monotonic()
at = lambda: f"[{time.monotonic() - start:4.1f}s]"

session = launch_session("review-utils", config, backend=terminal)
print(at(), "launch_session ->", session.status.value)
try:
    for _ in range(120):
        changed = monitor_session(session)
        print(at(), "monitor_session ->", session.status.value, "(changed)" if changed else "")
        if session.is_terminal:
            break
        time.sleep(1)
    review = capture_output(session, lines=100)
    print(at(), "capture_output ->")
    print(review)
finally:
    stop_session(session)
    print(at(), "stop_session ->", session.status.value, "| killed:", terminal.killed)
