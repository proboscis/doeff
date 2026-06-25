"""Tests for Claude-specific monitor status detection."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents.monitor import (
    MonitorState,
    SessionStatus,
    detect_status,
    has_claude_active_marker,
)


def _past_monitor_state() -> MonitorState:
    return MonitorState(last_output_at=datetime.now(timezone.utc) - timedelta(seconds=10))


def test_stable_claude_thinking_footer_stays_running() -> None:
    output = (
        "● Reading 1 file... (ctrl+o to expand)\n"
        "  ⎿  $ cat prompt.txt\n\n"
        "· Seasoning... (20s · ↓ 838 tokens · thinking with medium effort)\n\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "> \n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "  bypass permissions on (shift+tab to cycle) · esc to interrupt\n"
    )

    assert has_claude_active_marker(output)
    assert (
        detect_status(
            output,
            _past_monitor_state(),
            output_changed=False,
            has_prompt=True,
        )
        == SessionStatus.RUNNING
    )


def test_stable_claude_idle_prompt_can_still_block() -> None:
    output = (
        "作業を完了しました。\n\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "> \n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "  bypass permissions on (shift+tab to cycle) · paste again to expand\n"
    )

    assert not has_claude_active_marker(output)
    assert (
        detect_status(
            output,
            _past_monitor_state(),
            output_changed=False,
            has_prompt=True,
        )
        == SessionStatus.BLOCKED
    )
