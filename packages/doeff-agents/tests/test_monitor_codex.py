"""Tests for Codex-specific monitor status detection."""

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
    has_codex_active_marker,
    has_codex_idle_prompt,
    is_codex_turn_complete,
)

CODEX_PROMPT = "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}"
CLAUDE_PROMPT = "\N{HEAVY RIGHT-POINTING ANGLE QUOTATION MARK ORNAMENT}"


def _past_monitor_state() -> MonitorState:
    return MonitorState(last_output_at=datetime.now(timezone.utc) - timedelta(seconds=10))


def test_codex_worked_for_footer_does_not_mark_success() -> None:
    output = (
        "作業を完了しました。\n"
        "─ Worked for 12m 09s ─────────────────────────\n"
        f"{CODEX_PROMPT} Use /skills to list available skills\n"
        "gpt-5.5 xhigh fast · ~/repo\n"
    )

    assert detect_status(
        output,
        _past_monitor_state(),
        output_changed=True,
        has_prompt=False,
    ) == SessionStatus.RUNNING


def test_claude_thinking_spinner_is_running_not_awaiting_input() -> None:
    # The recon / trading L2 agents are headless Claude. While thinking, Claude
    # keeps the input box + "bypass permissions (shift+tab to cycle)" footer
    # (has_prompt True) and its spinner sits in the skipped status-bar lines
    # (output_changed False). Without detecting the active marker this reads
    # BLOCKED -> a false AWAITING_INPUT that aborts the L2 AwaitResult mid-think.
    output = (
        "● Reading account state from SBI...\n"
        "✽ Whatchamacalliting… (6m 10s · ↓ 15.7k tokens)\n"
        f"{CLAUDE_PROMPT} \n"
        "⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt\n"
    )
    state = _past_monitor_state()
    assert has_claude_active_marker(output)
    assert detect_status(
        output,
        state,
        output_changed=False,
        has_prompt=True,
    ) == SessionStatus.RUNNING


def test_claude_idle_without_active_marker_is_blocked() -> None:
    # Genuinely idle: no spinner / "esc to interrupt", output stable, prompt up.
    output = (
        "● Done.\n"
        f"{CLAUDE_PROMPT} \n"
        "⏵⏵ bypass permissions on (shift+tab to cycle)\n"
    )
    state = _past_monitor_state()
    assert not has_claude_active_marker(output)
    assert detect_status(
        output,
        state,
        output_changed=False,
        has_prompt=True,
    ) == SessionStatus.BLOCKED


def test_stable_codex_idle_prompt_marks_awaiting_input() -> None:
    output = (
        "検証結果を issue に反映しました。\n"
        f"{CODEX_PROMPT} Find and fix a bug in @filename\n"
        "gpt-5.5 xhigh fast · ~/repo\n"
    )
    state = _past_monitor_state()

    assert has_codex_idle_prompt(output)
    assert is_codex_turn_complete(output, state, output_changed=False)
    assert detect_status(
        output,
        state,
        output_changed=False,
        has_prompt=False,
    ) == SessionStatus.BLOCKED


def test_active_codex_turn_is_not_done() -> None:
    output = (
        "• Explored\n"
        "◦ Working (4m 51s • esc to interrupt)\n"
        f"{CODEX_PROMPT} Find and fix a bug in @filename\n"
        "gpt-5.5 xhigh fast · ~/repo\n"
    )

    assert has_codex_idle_prompt(output)
    assert has_codex_active_marker(output)
    assert not is_codex_turn_complete(output, _past_monitor_state(), output_changed=False)
    assert detect_status(
        output,
        _past_monitor_state(),
        output_changed=True,
        has_prompt=False,
    ) == SessionStatus.RUNNING
