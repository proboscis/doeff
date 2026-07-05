"""S15 solicitation counter survives restart (contract README S15, tag P,
mode M2).

The session reaches turn-end without a valid report, receives the first
corrective solicitation, and is then bounced (`harness.restart()`) BEFORE
it ever responds. `main()` clears every `awaiting_response` latch on
daemon startup by design (main.rs:578: "any 'awaiting_response' latches
... refer to retry prompts the previous process sent and that nobody is
monitoring anymore") -- but `result_solicitations_used` is a durable
column that is deliberately NEVER reset on restart (main.rs:309-315). The
new daemon must therefore deliver exactly ONE more solicitation (bringing
the total to 2, the default budget) and then fail -- NOT reset the
counter and deliver 2 more (which would silently double the effective
budget across a restart).

The lease-vs-restart harness defect the worker found here is now absorbed
into `AgentdHarness.restart()` itself (retry past LEASE_TTL_SECONDS; see
test_s10_payload_durability.py and README hazard 5).
"""

from __future__ import annotations

import time

from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."
SOLICITATION_MARKER = "AGENTD RESULT CONTRACT"


def _await_solicitation_landed(scenario, *, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if any(
            entry.get("event") == "keys"
            and entry.get("expect") == SOLICITATION_MARKER
            and entry.get("matched")
            for entry in scenario.journal()
        ):
            return True
        time.sleep(0.1)
    return False


def test_s15_counter_survives_restart() -> None:
    with AgentdHarness(extra_serve_args=["--prompt-judge-cmd", ""]) as harness:
        scenario = harness.scenario(
            "s15",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"render": "F-turn-activity-claude"},
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": SOLICITATION_MARKER, "timeout_s": 60}},
                {"render": "F-idle-claude"},
                # No report_result: the script parks here, deliberately
                # never responding to either solicitation.
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )

        assert _await_solicitation_landed(scenario, timeout_s=20.0), (
            f"first solicitation never landed\n{harness.log_text()}"
        )

        # Restart BEFORE the session ever responds. The pasted text lands
        # in the pty a moment before the daemon's own DB write settles
        # (tmux_send_keys sleeps ~1s before Enter), so this is a "restart
        # shortly after solicitation 1 was sent" probe, not a strict
        # "counter==1 confirmed" precondition -- the real assertion is the
        # FINAL total after the dust settles below.
        harness.restart()

        # The new daemon clears the (now-stale) awaiting_response latch,
        # re-evaluates turn-end, and must send exactly one more
        # solicitation before exhausting the budget.
        deadline = time.monotonic() + 30.0
        row = harness.session_row(scenario.session_id)
        while time.monotonic() < deadline and row["status"] != "failed":
            time.sleep(0.3)
            row = harness.session_row(scenario.session_id)

        assert row["status"] == "failed", (
            f"status={row['status']}\n" + harness.log_text()
        )
        assert row["result_solicitations_used"] == 2, (
            "the counter must survive the restart (2 total, not reset to "
            f"0 and re-delivered as 2 MORE): {row['result_solicitations_used']}"
        )
        assert "after 2 solicitation(s)" in (row["last_validation_error"] or ""), row[
            "last_validation_error"
        ]

        events = harness.events(scenario.session_id)
        solicited_events = [e for e in events if e["event_type"] == "session_result_solicited"]
        assert len(solicited_events) == 2, (
            f"expected exactly two solicitation events total across the restart: {events}"
        )
