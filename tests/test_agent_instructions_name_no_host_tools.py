"""The repository's agent instructions do not prescribe the maintainer's host tools.

Sessions opened inside agora read this repository's AGENTS.md too, and they have
no terminal multiplexer, no peer-messaging command and no profile switcher. A
2026-10-07 session followed host-only steps it read in shared instructions and
failed (agora-redesign #3904 / #3915). How to reach another session belongs to
the environment's own instructions, not to this repository.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOST_TOOL_WORDS = re.compile(r"\bherdr\b|\bagmsg\b|\bai usage\b", re.IGNORECASE)


def host_tool_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if HOST_TOOL_WORDS.search(line)]


def test_agents_md_names_no_host_tools() -> None:
    found = host_tool_lines((REPO / "AGENTS.md").read_text(encoding="utf-8"))
    assert found == [], f"AGENTS.md prescribes host tools: {found}"


def test_detector_flags_a_host_tool_step() -> None:
    sample = "- message the other session via herdr: `herdr pane send-text <id> hi`\n- use worktrees"
    assert host_tool_lines(sample) == [sample.splitlines()[0]]
