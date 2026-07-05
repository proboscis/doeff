"""S3 solicitation budget exhaustion (contract README S3, tag P, mode M2).

Turn-end is reached without a valid report, and the agent never responds
to either corrective solicitation. The monitor auto-recovers turn-end
detection after each solicitation because the F-turn-activity-claude
bullet (`⏺`) rendered earlier in the script stays inside the 100-line
tmux capture window (main.rs:3589's `tmux_capture(..., 100)`): once
`awaiting_response` clears on the next tick (main.rs:3629, the marker is
still visible so the clause fires again), turn-end re-evaluates. No
explicit re-render of the activity marker is needed between solicitations
-- confirmed empirically (see the worker's probe transcript): a script
that renders the marker exactly once naturally cycles through both
solicitations and then fails, ~1-2s apart with the judge disabled.

Same judge-default note as S2: `harness.py`'s `AgentdHarness.start()` now
disables the judge by default, so `extra_serve_args=["--prompt-judge-cmd",
""]` below is redundant but kept explicit for self-documentation.
"""

from __future__ import annotations

import json

from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."


def test_s3_solicitation_exhaustion() -> None:
    with AgentdHarness(extra_serve_args=["--prompt-judge-cmd", ""]) as harness:
        scenario = harness.scenario(
            "s3",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"render": "F-turn-activity-claude"},
                {"render": "F-idle-claude"},
                # No report_result call, ever: the script parks after this,
                # letting the monitor's bounded solicitation loop run out.
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=30.0)

        # wire: a terminal-without-result failure returns no payload
        assert outcome.result is None, (
            f"expected no result on exhausted solicitation budget: {outcome.result!r}\n"
            + harness.log_text()
        )

        # row: failed, reason names the exhausted budget, cause frozen per
        # the TerminalCause table, counter reflects both solicitations
        row = harness.session_row(scenario.session_id)
        assert row["status"] == "failed", (
            f"status={row['status']}\n" + harness.log_text()
        )
        assert row["last_validation_error"] is not None
        assert "after 2 solicitation(s)" in row["last_validation_error"], row["last_validation_error"]
        assert row["result_solicitations_used"] == 2

        cause = json.loads(row["terminal_cause_json"])
        assert cause["category"] == "run_failed", cause
        assert cause["retryable"] is False, cause

        # journal/events: exactly two solicitations were sent
        events = harness.events(scenario.session_id)
        solicited_events = [e for e in events if e["event_type"] == "session_result_solicited"]
        assert len(solicited_events) == 2, f"expected exactly two solicitation events: {events}"
