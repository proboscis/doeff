# The scene under launch_session: before it opens the terminal, launch_session runs two doeff programs,
# adapter.available() and adapter.pre_launch(). They yield effects; the io_root's handlers answer them. Here the
# io_root is recording_root (stand_ins.py): an in-memory world answers, and a recorder handler prints each effect
# with its answer. Then the same launch in a world without ~/.claude.json: the program raises at its check, and no
# terminal session is opened.
from pathlib import Path

from stand_ins import HOME, ScriptedTerminal, recorder, recording_root, world

from doeff import run
# The old (procedural) chapter-19 video measured these session procedures; they are internal to doeff-agents now
# (card ki-4b53dcee28f3) — the rewritten video measures the effects instead.
from doeff_agents import AgentType
from doeff_agents._session import launch_session
from doeff_agents.adapters.base import LaunchConfig
from doeff_agents.io_fake import with_fake_io

config = LaunchConfig(agent_type=AgentType.CLAUDE, work_dir=Path.cwd(), prompt="Review the code in src/utils.py")

terminal = ScriptedTerminal(work={"src/utils.py": 1.0}, review={"src/utils.py": ["1. ok."]})
print("launch_session('review-utils', config, io_root=recording_root):")
session = launch_session("review-utils", config, backend=terminal, io_root=recording_root)
print("-> session", session.status.value, "| terminal sessions:", terminal.list_sessions(), "| typed:", [keys.split()[0] for keys in terminal.panes["review-utils"]["sends"]])

empty = world()
empty.files.pop(f"{HOME}/.claude.json")
terminal = ScriptedTerminal(work={"src/utils.py": 1.0}, review={"src/utils.py": ["1. ok."]})
print("the same launch in a world without ~/.claude.json:")
try:
    launch_session("review-utils", config, backend=terminal, io_root=lambda program: run(with_fake_io(empty, recorder(program))))
except RuntimeError as error:
    print("-> RuntimeError:", str(error).splitlines()[0])
print("-> terminal sessions:", terminal.list_sessions())
