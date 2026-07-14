"""S6b judge-unavailable variants at the STALL judgment point (contract
README S6b, tag P, mode M2).

ADR-DOE-AGENTS-002 R7 distinguishes two judge-unavailable shapes, and the
stall site must fail LOUDLY for both (there is no other path that can
unblock a frozen pane):

  (1) judge disabled (`--prompt-judge-cmd ""`, the suite-wide default the
      harness injects): the first stall evaluation skips the attempt loop
      entirely -> immediate typed failure, reason prefix
      `interactive-prompt-blocked:` + `no prompt judge configured`,
      prompt_unblock_attempts stays 0.
  (2) judge command is a nonexistent path: `sh -c` spawns, produces no
      JSON, the verdict parse errors -> typed failure with `prompt judge
      failed` in the reason; the attempt was consumed
      (prompt_unblock_attempts == 1).

The OTHER half of the README S6b row -- "turn-end point degrades to
solicitation when the judge is disabled" -- is witnessed by S2/S3: the
whole M2 batch runs with the judge disabled by harness default, and those
scenarios still receive the full bounded solicitation loop at turn-end.

Same stall script as S6 (see test_s6_stall_judge.py for the hazard 3/4
choreography).
"""

import json
import time

from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."

STALL_SCRIPT = [
    {"render": "F-idle-claude"},
    {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
    {"render": "F-active-codex"},
    {"await_monitor_ack": {"timeout_s": 30}},
    {"scroll": 110},
    {"render": "F-frozen"},
]


def _await_failed_row(harness, session_id, *, timeout_s: float):
    deadline = time.monotonic() + timeout_s
    row = harness.session_row(session_id)
    while time.monotonic() < deadline and row["status"] != "failed":
        time.sleep(0.3)
        row = harness.session_row(session_id)
    return row


def test_s6b_judge_disabled_fails_stall_immediately() -> None:
    # No --prompt-judge-cmd in extra_serve_args -> the harness injects
    # `--prompt-judge-cmd ""` (judge disabled), the suite-wide default.
    with AgentdHarness(extra_serve_args=["--prompt-stall-secs", "2"]) as harness:
        scenario = harness.scenario("s6b-disabled", STALL_SCRIPT)
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=45.0)
        assert outcome.result is None

        row = _await_failed_row(harness, scenario.session_id, timeout_s=10.0)
        assert row["status"] == "failed", (
            f"status={row['status']}\n" + harness.log_text()
        )
        reason = row["last_validation_error"] or ""
        assert reason.startswith("interactive-prompt-blocked:"), reason
        assert "no prompt judge configured" in reason, reason
        # the stall site never consumed an attempt: there was no judge to run
        assert row["prompt_unblock_attempts"] == 0, row["prompt_unblock_attempts"]
        cause = json.loads(row["terminal_cause_json"])
        assert cause["category"] == "interactive_prompt_blocked", cause
        assert cause["retryable"] is False, cause

        types = [e["event_type"] for e in harness.events(scenario.session_id)]
        assert "session_prompt_unblocked" not in types, types
        assert "session_prompt_judge_inconclusive" not in types, types


def test_s6b_judge_error_fails_stall_typed() -> None:
    with AgentdHarness(
        extra_serve_args=[
            "--prompt-judge-cmd",
            "/nonexistent/conformance-prompt-judge",
            "--prompt-stall-secs",
            "2",
        ]
    ) as harness:
        scenario = harness.scenario("s6b-judge-error", STALL_SCRIPT)
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=45.0)
        assert outcome.result is None

        row = _await_failed_row(harness, scenario.session_id, timeout_s=10.0)
        assert row["status"] == "failed", (
            f"status={row['status']}\n" + harness.log_text()
        )
        reason = row["last_validation_error"] or ""
        assert reason.startswith("interactive-prompt-blocked:"), reason
        assert "prompt judge failed" in reason, reason
        # the attempt was consumed before the judge errored (main.rs:3836)
        assert row["prompt_unblock_attempts"] == 1, row["prompt_unblock_attempts"]
        cause = json.loads(row["terminal_cause_json"])
        assert cause["category"] == "interactive_prompt_blocked", cause
        assert cause["retryable"] is False, cause

        types = [e["event_type"] for e in harness.events(scenario.session_id)]
        assert "session_prompt_unblocked" not in types, types
