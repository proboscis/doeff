"""S6 interactive-prompt stall watchdog (contract README S6, tag P, mode M2).

A pane that goes byte-identical past `--prompt-stall-secs` while showing
neither the idle REPL glyph nor an active-work marker is blocked on
something turn-end detection can never see (ADR-DOE-AGENTS-002 R5). The
stall watchdog (main.rs:3801-3890) fires only when ALL hold: status
running, RunToCompletion, `awaiting_response` latch cleared,
`observed_active_at` set, no active marker, no idle prompt, and
last_output_change older than the stall threshold.

Script shape (hazards 3+4): the launch-time idle glyph stays live in the
monitor's capture-100 window, so the script scrolls >100 lines before
rendering F-frozen — and it retires the active frame only AFTER
`await_monitor_ack` confirms the monitor consumed it (the latch clears
only on an observed active marker, main.rs:3629; a frame retired inside
the launch blind window never existed).

Two variants:

  (1) scripted judge says blocked -> agentd sends the unblock keys and
      records `session_prompt_unblocked`, once per stall evaluation, until
      `prompt_unblock_attempts` reaches the bound (3) -- the pane never
      reacts (the parked fake discards its stdin), so the next stall tick
      fails the session loudly: reason prefix `interactive-prompt-blocked:`,
      cause interactive_prompt_blocked retryable=false (R7: bounded, never
      an infinite wait).
  (2) scripted judge says NOT blocked (empty verdict table) -> each
      attempt records `session_prompt_judge_inconclusive` instead; the
      attempt budget bounds these rounds too, same typed failure.

The frames are agent-agnostic on the monitor side (idle/active detection
checks both codex and claude markers), so the session launches as claude
(no CODEX_HOME launch gate) while reusing the robust `working (` codex
active marker.
"""

from __future__ import annotations

import json
import shlex
import sys
import time

from harness import JUDGE_SCRIPT, RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."

STALL_SCRIPT = [
    {"render": "F-idle-claude"},
    {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
    # clear the awaiting_response latch: only an observed active marker /
    # turn activity does that (main.rs:3629)
    {"render": "F-active-codex"},
    {"await_monitor_ack": {"timeout_s": 30}},
    # retire BOTH the launch idle glyph and the active marker: the idle
    # glyph survives in capture-100 (hazard 3), the active marker in
    # tail-30 -- either one visible keeps the stall branch unreachable
    {"scroll": 110},
    {"render": "F-frozen"},
    # script exhausted -> the fake PARKS, discarding stdin: unblock keys
    # arrive but the pane never changes, which is exactly the stall the
    # watchdog must bound
]


def _judge_cmd(table_path, judge_journal) -> str:
    return (
        f"CONFORMANCE_JUDGE_TABLE={shlex.quote(str(table_path))} "
        f"CONFORMANCE_JUDGE_JOURNAL={shlex.quote(str(judge_journal))} "
        f"{shlex.quote(sys.executable)} {shlex.quote(str(JUDGE_SCRIPT))}"
    )


def _await_failed_row(harness, session_id, *, timeout_s: float):
    deadline = time.monotonic() + timeout_s
    row = harness.session_row(session_id)
    while time.monotonic() < deadline and row["status"] != "failed":
        time.sleep(0.3)
        row = harness.session_row(session_id)
    return row


def _judge_entries(judge_journal) -> list[dict]:
    if not judge_journal.exists():
        return []
    return [
        json.loads(line)
        for line in judge_journal.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_s6_stall_bounded_judge_exhaustion(tmp_path) -> None:
    table_path = tmp_path / "judge-table.json"
    judge_journal = tmp_path / "judge-journal.jsonl"
    table_path.write_text(
        json.dumps(
            [
                {
                    "contains": "Password:",
                    "verdict": {
                        # "Down" is whitelisted (main.rs:3247) and its echo
                        # is at worst a cursor move -- the parked fake never
                        # reacts, so the pane stays frozen either way
                        "blocked": True,
                        "keys": ["Down"],
                        "reason": "login prompt",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    with AgentdHarness(
        extra_serve_args=[
            "--prompt-judge-cmd",
            _judge_cmd(table_path, judge_journal),
            "--prompt-stall-secs",
            "2",
        ]
    ) as harness:
        scenario = harness.scenario("s6", STALL_SCRIPT)
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=60.0)
        assert outcome.result is None, (
            f"stalled session must not produce a result: {outcome.result!r}\n"
            + harness.log_text()
        )

        row = _await_failed_row(harness, scenario.session_id, timeout_s=10.0)
        assert row["status"] == "failed", (
            f"status={row['status']}\n" + harness.log_text()
        )
        reason = row["last_validation_error"] or ""
        assert reason.startswith("interactive-prompt-blocked:"), reason
        assert "unblock attempt(s) exhausted" in reason, reason
        assert row["prompt_unblock_attempts"] == 3, row["prompt_unblock_attempts"]
        cause = json.loads(row["terminal_cause_json"])
        assert cause["category"] == "interactive_prompt_blocked", cause
        assert cause["retryable"] is False, cause

        events = harness.events(scenario.session_id)
        unblocked = [e for e in events if e["event_type"] == "session_prompt_unblocked"]
        assert len(unblocked) == 3, [e["event_type"] for e in events]

        judged = _judge_entries(judge_journal)
        assert len(judged) >= 3 and all(e["verdict"]["blocked"] for e in judged), judged


def test_s6_stall_judge_inconclusive_is_bounded_too(tmp_path) -> None:
    table_path = tmp_path / "judge-table.json"
    judge_journal = tmp_path / "judge-journal.jsonl"
    # empty verdict table -> the scripted judge always answers
    # {"blocked": false} ("no scripted verdict matched")
    table_path.write_text(json.dumps([]), encoding="utf-8")
    with AgentdHarness(
        extra_serve_args=[
            "--prompt-judge-cmd",
            _judge_cmd(table_path, judge_journal),
            "--prompt-stall-secs",
            "2",
        ]
    ) as harness:
        scenario = harness.scenario("s6-inconclusive", STALL_SCRIPT)
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=60.0)
        assert outcome.result is None

        row = _await_failed_row(harness, scenario.session_id, timeout_s=10.0)
        assert row["status"] == "failed", (
            f"status={row['status']}\n" + harness.log_text()
        )
        reason = row["last_validation_error"] or ""
        assert reason.startswith("interactive-prompt-blocked:"), reason
        assert "unblock attempt(s) exhausted" in reason, reason
        assert row["prompt_unblock_attempts"] == 3, row["prompt_unblock_attempts"]
        cause = json.loads(row["terminal_cause_json"])
        assert cause["category"] == "interactive_prompt_blocked", cause
        assert cause["retryable"] is False, cause

        events = harness.events(scenario.session_id)
        types = [e["event_type"] for e in events]
        inconclusive = [t for t in types if t == "session_prompt_judge_inconclusive"]
        assert len(inconclusive) == 3, types
        assert "session_prompt_unblocked" not in types, types

        judged = _judge_entries(judge_journal)
        assert len(judged) >= 3 and not any(e["verdict"]["blocked"] for e in judged), judged
