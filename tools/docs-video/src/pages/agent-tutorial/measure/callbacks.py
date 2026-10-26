# The callback scene: Step 3 of docs/19-agent-tutorial.md (review_progress.py). monitor_session calls
# on_status_change(old, new, output) only when the status changes; ReviewProgress prints each change with the
# seconds since it started and keeps the history. Polls every 0.5 s, as the page does, on the scripted terminal of
# stand_ins.py: a 3 s review that stops after 1 s to ask whether it may run the tests. A BLOCKED session waits for
# input (the page's "Handle BLOCKED status"), so the loop answers it with send_message, as the doeff-agents README
# does. Prints the callback's lines, how many times monitor_session ran, and the history.
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from stand_ins import ScriptedTerminal

# The old (procedural) chapter-19 video measured these session procedures; they are internal to doeff-agents now
# (card ki-4b53dcee28f3) — the rewritten video measures the effects instead.
from doeff_agents import AgentType, SessionStatus
from doeff_agents._session import monitor_session, send_message, session_scope
from doeff_agents.adapters.base import LaunchConfig


@dataclass
class ReviewProgress:
    file_path: str
    started_at: datetime = field(default_factory=datetime.now)
    status_history: list[tuple[float, SessionStatus]] = field(default_factory=list)

    def on_status_change(self, old: SessionStatus, new: SessionStatus, output: str | None) -> None:
        elapsed = (datetime.now() - self.started_at).total_seconds()
        self.status_history.append((elapsed, new))
        print(f"  [{elapsed:.1f}s] {old.value} -> {new.value}")


terminal = ScriptedTerminal(
    work={"src/utils.py": 3.0},
    review={"src/utils.py": ["1. ok."]},
    ask={"src/utils.py": (1.0, "Run pytest tests/test_utils.py?")},
)
config = LaunchConfig(agent_type=AgentType.CLAUDE, work_dir=Path.cwd(), prompt="Review the code in src/utils.py.")
progress = ReviewProgress("src/utils.py")
polls = 0
print("Reviewing: src/utils.py")
with session_scope("review-utils", config, backend=terminal) as session:
    while not session.is_terminal:
        monitor_session(session, on_status_change=progress.on_status_change)
        polls += 1
        if session.status == SessionStatus.BLOCKED:
            send_message(session, "1")
            print(f"  [{(datetime.now() - progress.started_at).total_seconds():.1f}s] send_message(session, '1')")
        time.sleep(0.5)
print(f"monitor_session ran {polls} times; on_status_change ran {len(progress.status_history)} times")
print("status_history:", [(round(t, 1), s.value) for t, s in progress.status_history])
print("after the with block:", session.status.value)
