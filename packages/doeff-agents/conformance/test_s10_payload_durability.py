"""S10 payload durability across restart (contract README S10, tag P, mode M2).

Two scenarios:

  (a) golden path to `done`, `harness.restart()`, then confirm
      `await_result`/`session_row` still show the payload (COALESCE
      persistence discipline, main.rs:2339 area) -- then the DRIVER
      re-speaks `report_result` for the same session and must see the
      idempotent `already_reported: true` acknowledgement.
  (b) drive a terminal-WITHOUT-result session (S3-style exhaustion), then
      the DRIVER reports a result -> must be rejected as already-terminal
      (-32003).

Harness defect found by the S10 worker, since absorbed into
`AgentdHarness.restart()`: `doeff-agentd` has no SIGTERM lease-release
handler -- `acquire_lease_in_transaction` (main.rs:1092) rejects a fresh
`serve` with "lease is active" until the previous lease's `expires_at`
(heartbeat + LEASE_TTL_SECONDS=10, main.rs:21) passes, regardless of
whether the old owner PID is actually still alive, so a naive
terminate-then-start restart fails non-deterministically inside that
window. `restart()` now retries past the TTL (README hazard 5).

Oracle-vs-contract discrepancy shared with S4: the `report-result-mcp` MCP
relay (main.rs:863) collapses BOTH `already_reported: true` and a fresh
accept into the same generic `{"content":[{"text":"result recorded"}],
"isError":false}` response, and collapses the -32003 rejection into a
tool-error TEXT with no numeric code (main.rs:908 `relay_report_result`
discards `RpcResponse.error_code`). Neither `already_reported` nor -32003
is observable through that channel. The only way to observe the wire-level
contract literally named in the README's checklist (d)/(e) is to speak
`session.report_result` directly against the daemon's OWN control socket
-- which `AgentdClient.request()` (the sanctioned public wire client) is
built to do generically. This test uses `harness.client.request(...)`
for the driver-side re-report instead of spawning `report-result-mcp`,
and documents why. Confirmed empirically: the raw wire call returns
`{"accepted": true, "already_reported": true}` and raises
`AgentdClientError(..., error_code=-32003)` respectively.
"""

from __future__ import annotations

import json

from doeff_agents.agentd_client import AgentdClientError
from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."
PAYLOAD = {"summary": "durable", "ok": True}


def test_s10a_payload_survives_restart_and_rereport_is_idempotent() -> None:
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s10a",
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
        assert outcome.result == PAYLOAD, harness.log_text()

        harness.restart()

        # wire: get_session / await_result still show done + the payload
        post_restart_outcome = harness.client.await_result(
            scenario.session_id, timeout_seconds=10.0
        )
        assert post_restart_outcome.result == PAYLOAD, (
            f"payload did not survive restart: {post_restart_outcome.result!r}"
        )
        snapshot = harness.client.get_session(scenario.session_id)
        assert snapshot is not None and snapshot.status.value == "done"

        # row: still persisted on disk
        row = harness.session_row(scenario.session_id)
        assert row["status"] == "done"
        assert json.loads(row["result_payload_json"]) == PAYLOAD

        # driver speaks the wire-level report_result directly (see
        # docstring for why the MCP relay can't show this): a duplicate
        # report against a terminal-with-result session is idempotent.
        wire_response = harness.client.request(
            "session.report_result",
            {"session_id": scenario.session_id, "payload": PAYLOAD},
        )
        assert wire_response == {"accepted": True, "already_reported": True}, wire_response


def test_s10b_report_after_terminal_without_result_is_rejected() -> None:
    with AgentdHarness(extra_serve_args=["--prompt-judge-cmd", ""]) as harness:
        scenario = harness.scenario(
            "s10b",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"render": "F-turn-activity-claude"},
                {"render": "F-idle-claude"},
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=30.0)
        assert outcome.result is None
        row = harness.session_row(scenario.session_id)
        assert row["status"] == "failed", harness.log_text()

        try:
            harness.client.request(
                "session.report_result",
                {"session_id": scenario.session_id, "payload": PAYLOAD},
            )
            raise AssertionError("expected report_result to be rejected as already-terminal")
        except AgentdClientError as exc:
            assert exc.error_code == -32003, exc
