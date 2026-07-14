"""S1 golden path (contract README S1, tag P, mode M2).

launch → prompt received → turn activity → report_result(valid) → idle →
done; await_result returns the payload byte-faithfully; the row persists it.

This file is the TEMPLATE for the remaining scenarios: worker-implemented
scenarios (S2+) follow this exact shape — script, launch_m2, wire assert,
journal assert, row assert — and must not edit harness.py without
reporting back.
"""

import json

from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."
PAYLOAD = {"summary": "golden", "ok": True}


def test_s1_golden_path() -> None:
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s1",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"render": "F-turn-activity-claude"},
                {"report_result": {"payload": PAYLOAD}},
                {"render": "F-idle-claude"},
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=25.0)

        # wire: the payload comes back byte-faithfully (ADR 0035)
        assert outcome.result == PAYLOAD, (
            f"await_result payload drifted: {outcome.result!r}\n{harness.log_text()}"
        )
        assert outcome.validation_error is None

        # journal: the agent really received the prompt and a report ack
        events = {entry["event"] for entry in scenario.journal()}
        assert "started" in events
        prompt_hits = [
            entry
            for entry in scenario.journal()
            if entry["event"] == "keys" and entry["matched"]
        ]
        assert prompt_hits, "prompt paste never reached the agent tty"
        report_entries = [
            entry for entry in scenario.journal() if entry["event"] == "report_result"
        ]
        assert report_entries and "error" not in json.loads(
            report_entries[0]["response"]
        ).get("result", {}), f"report_result not accepted: {report_entries}"

        # row: done + payload persisted (checklist (d) first half)
        row = harness.session_row(scenario.session_id)
        assert row["status"] == "done", (
            f"status={row['status']} err={row['last_validation_error']}\n"
            + harness.log_text()
        )
        assert json.loads(row["result_payload_json"]) == PAYLOAD
        # no solicitation was needed on the golden path
        assert row["result_solicitations_used"] == 0
