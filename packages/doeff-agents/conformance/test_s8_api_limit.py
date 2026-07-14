"""S8 api-limit taxonomy (contract README S8a/S8b, tag P, mode M2).

S8a: an api-limit marker alone classifies the session `blocked_api`, which
is deliberately NON-terminal (main.rs:1918 active_statuses, :2912
is_await_terminal_status): level-triggered, the session may recover when
the pane changes, and `session.await_result` keeps blocking (-32000 on a
short budget).

S8b: the RateLimited/retryable=true cause is written by the reason-less
failed-output mapping (main.rs:3895-3905): a failure marker plus api-limit
text in the tail fails the session AND stamps TerminalCause RateLimited
retryable=true — the wire fact ACP's ADR 0042 transient classification
sits downstream of.
"""

import json
import time

from doeff_agents.effects import AwaitStatus
from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Trip the provider limit."


def _poll_status(harness: AgentdHarness, session_id: str, want: str, budget_s: float) -> str:
    deadline = time.monotonic() + budget_s
    status = "<never read>"
    while time.monotonic() < deadline:
        status = str(harness.session_row(session_id)["status"])
        if status == want:
            return status
        time.sleep(0.2)
    return status


def test_s8a_api_limit_is_nonterminal_blocked_api() -> None:
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s8a",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"render": "F-api-limit"},
            ],
        )
        scenario.launch_m2(prompt=PROMPT, expected_result={"payload_schema": RESULT_SCHEMA})

        status = _poll_status(harness, scenario.session_id, "blocked_api", 15.0)
        assert status == "blocked_api", f"status={status}\n{harness.log_text()}"

        # non-terminal by design: a short await BLOCKS until its budget
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=2.0)
        assert outcome.status is AwaitStatus.TIMED_OUT, outcome

        row = harness.session_row(scenario.session_id)
        assert row["terminal_cause_json"] is None, row["terminal_cause_json"]
        assert any(
            e["event_type"] == "session_blocked" for e in harness.events(scenario.session_id)
        )


def test_s8b_failed_with_api_limit_output_maps_rate_limited_retryable() -> None:
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s8b",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {
                    "render": {
                        "literal": "\nfatal error: provider says rate limit exceeded\n"
                    }
                },
            ],
        )
        scenario.launch_m2(prompt=PROMPT, expected_result={"payload_schema": RESULT_SCHEMA})

        status = _poll_status(harness, scenario.session_id, "failed", 15.0)
        assert status == "failed", f"status={status}\n{harness.log_text()}"

        row = harness.session_row(scenario.session_id)
        cause = json.loads(row["terminal_cause_json"])
        # serde(rename_all = "snake_case") on TerminalCauseCategory (main.rs:329)
        assert cause["category"] == "rate_limited", cause
        assert cause["retryable"] is True, cause
