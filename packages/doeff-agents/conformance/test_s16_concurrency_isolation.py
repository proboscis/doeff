"""S16 concurrency isolation (contract README S16, tag P, mode M2).

ONE daemon, TWO sessions. Session B is launched FIRST and renders the
F-failed marker right after receiving its prompt; session A then runs the
S1 golden path to completion. DOE-004 R3: one session's failure must not
take the other one down.

Isolation granularity in the Rust oracle is the TICK, not the session:
`run_worker_tick` (main.rs:3418) catches panics/errors so the monitor
thread survives, and `monitor_once` iterates all active sessions in one
tick body. The Hy implementation must satisfy per-SESSION isolation; the
observable assert is deliberately the common denominator ("the other
session completes"), witnessed here entirely over the wire
(`await_result` for both sessions).

The harness serves with `--max-running 4`, so two concurrent sessions
never trip the admission cap.
"""

import json

from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."
PAYLOAD = {"summary": "isolated golden path", "ok": True}


def test_s16_failing_session_does_not_break_the_other() -> None:
    with AgentdHarness(extra_serve_args=["--prompt-judge-cmd", ""]) as harness:
        # B first: reaches `failed` while A is still mid-flight
        scenario_b = harness.scenario(
            "s16b",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"render": "F-failed"},
            ],
        )
        scenario_b.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )

        scenario_a = harness.scenario(
            "s16a",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"render": "F-turn-activity-claude"},
                {"report_result": {"payload": PAYLOAD}},
                {"render": "F-idle-claude"},
            ],
        )
        scenario_a.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )

        # wire: A completes done with the byte-faithful payload even though
        # B failed next to it on the same monitor loop
        outcome_a = harness.client.await_result(scenario_a.session_id, timeout_seconds=40.0)
        assert outcome_a.result == PAYLOAD, (
            f"golden-path session was disturbed: {outcome_a.result!r}\n"
            + harness.log_text()
        )

        # wire: B is terminal-without-result with the frozen-table cause
        outcome_b = harness.client.await_result(scenario_b.session_id, timeout_seconds=40.0)
        assert outcome_b.result is None, (
            f"failed session must not carry a result: {outcome_b.result!r}"
        )

        row_a = harness.session_row(scenario_a.session_id)
        assert row_a["status"] == "done", (
            f"status={row_a['status']}\n" + harness.log_text()
        )
        assert json.loads(row_a["result_payload_json"]) == PAYLOAD

        row_b = harness.session_row(scenario_b.session_id)
        assert row_b["status"] == "failed", (
            f"status={row_b['status']}\n" + harness.log_text()
        )
        cause_b = json.loads(row_b["terminal_cause_json"])
        assert cause_b["category"] == "run_failed", cause_b
        assert cause_b["retryable"] is False, cause_b
