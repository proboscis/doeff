"""S4 schema-invalid report then fix (contract README S4, tag P, mode M2).

The agent reports a schema-invalid payload (rejected, not persisted, never
re-validated per ADR 0035 R4), then ends its turn. The turn-end-without-
result branch solicits a corrective report; the agent then reports a
valid payload and the session finalizes `done`.

Oracle-vs-contract discrepancy (README S4 / task brief said "the journal's
first report_result response contains -32002"): OBSERVED behavior is that
the numeric JSON-RPC error code never reaches the agent. `report_result`
is spoken over the `report-result-mcp` MCP relay (main.rs:863
handle_report_result_tool_call), which forwards only the message TEXT
from `relay_report_result` (main.rs:908, which discards
`RpcResponse.error_code` -- see the field comment at main.rs:213) and
wraps it as a tool-level error: `{"content":[...],"isError":true}`
(main.rs:949 mcp_tool_error). The literal numeric code -32002 is an
internal wire-protocol detail between `report-result-mcp` and the serving
daemon (`session.report_result`'s own RpcError, main.rs:104); it is never
serialized into the MCP `tools/call` response the agent's report_result()
helper receives. Confirmed empirically: the rejected report's journalled
response is exactly
`{"result":{"content":[{"text":"Error: reported result does not satisfy
its schema: 'payload' is missing required field 'ok'","type":"text"}],
"isError":true}}` -- no "-32002" substring anywhere. This test asserts the
OBSERVED discriminator (`isError: true` + the schema-rejection text)
instead. Flagged in the final report.

Same judge-default note as S2/S3: `harness.py` now disables the judge by
default; `extra_serve_args=["--prompt-judge-cmd", ""]` below is redundant
but kept explicit.
"""

import json

from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."
PAYLOAD = {"summary": "fixed after rejection", "ok": True}
SOLICITATION_MARKER = "AGENTD RESULT CONTRACT"


def test_s4_schema_invalid_then_fix() -> None:
    with AgentdHarness(extra_serve_args=["--prompt-judge-cmd", ""]) as harness:
        scenario = harness.scenario(
            "s4",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"render": "F-turn-activity-claude"},
                {"report_result": "schema_invalid"},
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": SOLICITATION_MARKER, "timeout_s": 30}},
                {"report_result": {"payload": PAYLOAD}},
                {"render": "F-idle-claude"},
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=30.0)

        # wire: the final (valid) payload wins, byte-faithfully
        assert outcome.result == PAYLOAD, (
            f"await_result payload drifted: {outcome.result!r}\n{harness.log_text()}"
        )
        assert outcome.validation_error is None

        # journal: the FIRST report_result call was rejected as a tool
        # error (see docstring for why this is not a literal "-32002")
        report_entries = [
            entry for entry in scenario.journal() if entry["event"] == "report_result"
        ]
        assert len(report_entries) == 2, report_entries
        first_response = json.loads(report_entries[0]["response"])
        assert first_response["result"]["isError"] is True, report_entries[0]
        assert "does not satisfy its schema" in first_response["result"]["content"][0]["text"], (
            report_entries[0]
        )
        second_response = json.loads(report_entries[1]["response"])
        assert second_response["result"]["isError"] is False, report_entries[1]

        # row: done, final payload is the valid one, not the rejected draft
        row = harness.session_row(scenario.session_id)
        assert row["status"] == "done", (
            f"status={row['status']} err={row['last_validation_error']}\n"
            + harness.log_text()
        )
        assert json.loads(row["result_payload_json"]) == PAYLOAD

        # events: exactly one rejection was recorded (never re-validated)
        events = harness.events(scenario.session_id)
        rejected_events = [e for e in events if e["event_type"] == "session_result_rejected"]
        assert len(rejected_events) == 1, f"expected exactly one rejection event: {events}"
