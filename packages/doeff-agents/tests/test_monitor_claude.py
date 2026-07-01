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
    evolve_status,
    has_claude_active_marker,
    has_claude_background_shell_marker,
    is_waiting_for_input,
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
        "No, and tell Claude what to do differently\n"
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


def test_stable_claude_permission_footer_alone_does_not_block() -> None:
    output = (
        "⏺ Bash(uv run pytest packages/doeff-agents/tests -q)\n"
        "  ⎿  Running…\n\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "> \n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents\n"
    )

    assert not is_waiting_for_input(output)
    assert (
        detect_status(
            output,
            _past_monitor_state(),
            output_changed=False,
            has_prompt=is_waiting_for_input(output),
        )
        is None
    )


def test_stale_claude_blocked_state_clears_without_current_prompt() -> None:
    output = (
        "⏺ Called sbi\n"
        "  ⎿  Running broker query...\n\n"
        "✻ Marinating… (40s · ↓ tokens)\n\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "❯\u00a0\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents\n"
    )
    detected = detect_status(
        output,
        _past_monitor_state(),
        output_changed=False,
        has_prompt=is_waiting_for_input(output),
    )

    assert detected is None
    assert (
        evolve_status(
            SessionStatus.BLOCKED,
            detected,
            has_prompt=is_waiting_for_input(output),
        )
        == SessionStatus.RUNNING
    )


def test_stable_claude_background_shell_stays_running() -> None:
    output = (
        "● Waiting through lunch (~58 min remaining). I'll sleep in foreground "
        "chunks within this same process, re-checking periodically.\n\n"
        '● Bash(until [ "$(TZ=Asia/Tokyo date +%H%M)" -ge 1230 ]; do '
        'sleep 20; done; TZ=Asia/Tokyo date +"%Y-%m-%d %H:%M:%S JST")\n'
        "  ⎿  Running in the background (↓ to manage)\n\n"
        "· Waiting for afternoon open… (2m 49s · ↑ 8.9k tokens)\n"
        "  ⎿  ✔ Reconcile broker state pre-open\n"
        "     ◼ Wait until 12:30 JST afternoon open\n"
        "     ◻ Open plan positions (nariyuki/day)\n\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "> \n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "  ⏵⏵ bypass permissions on · 1 shell · ctrl+t to hide tasks · ← for agents · …\n"
    )

    assert not has_claude_active_marker(output)
    assert has_claude_background_shell_marker(output)
    assert (
        detect_status(
            output,
            _past_monitor_state(),
            output_changed=False,
            has_prompt=True,
        )
        == SessionStatus.RUNNING
    )


def test_stable_claude_shell_still_running_stays_running() -> None:
    output = (
        "✻ Cooked for 12s · 1 shell still running\n\n"
        "  5 tasks (1 done, 1 in progress, 3 open)\n"
        "  ✔ Reconcile broker state pre-open\n"
        "  ◼ Wait until 12:30 JST afternoon open\n\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "> \n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "  ⏵⏵ bypass permissions on · 1 shell · ctrl+t to hide tasks · ← for agents · …\n"
    )

    assert has_claude_background_shell_marker(output)
    assert (
        detect_status(
            output,
            _past_monitor_state(),
            output_changed=False,
            has_prompt=True,
        )
        == SessionStatus.RUNNING
    )
