"""S8 api-limit taxonomy (contract README S8a/S8b/S8c/S8d, tag P, mode M2).

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

S8c/S8d (issue #557, canonical Hy host only): the terminal-time tail-30
snapshot is inherently racy — the limit wording scrolls out before the
terminal classification runs (measured fleet-wide 2026-07-20/23: every
turn-end-without-result terminal in the window classified run_failed/
retryable=false against a bare idle-prompt tail, latching ACP's steward
fleet on attend-failed). The fix is a durable latch: any observation of
the api-limit marker during the attempt is persisted on the session row
(`api_limit_observed_at`) and the terminal classification distills it to
rate_limited/retryable=true even when the terminal tail is clean.
  S8c: turn-end-without-result path (the incident's exact shape).
  S8d: failure-marker path (S8b with the limit text scrolled out).
"""

import json
import os
import time

import pytest
from doeff_agents.effects import AwaitStatus
from harness import RESULT_SCHEMA, AgentdHarness

# Transfer-gate seam (harness.build_agentd): the durable api-limit latch is
# canonical-Hy-host behavior (issue #557); the retired Rust reference
# implementation (issue #555) predates it and is not a correctness reference.
HY_GATE = bool(os.environ.get("CONFORMANCE_AGENTD_BIN"))

PROMPT = "Trip the provider limit."
SOLICITATION_MARKER = "AGENTD RESULT CONTRACT"
LIMIT_TEXT = "rate limit exceeded"


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


@pytest.mark.skipif(
    not HY_GATE,
    reason=(
        "S8c asserts the issue #557 durable api-limit latch on the canonical "
        "Hy host; the retired Rust implementation predates it"
    ),
)
def test_s8c_limit_scrolled_out_before_turn_end_terminal_maps_rate_limited() -> None:
    """The 2026-07-20/23 incident shape: the limit wording is visible only
    mid-attempt, solicitations fire and produce nothing, the pane returns to
    a bare idle prompt, and the solicitation budget exhausts against a tail
    with no limit text in any marker window."""
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s8c",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                # ⏺ stays inside the 100-line capture window for the rest of
                # the scenario: it clears awaiting_response after each
                # solicitation so turn-end re-arms (same physics as S3).
                {"render": "F-turn-activity-claude"},
                {"render": "F-api-limit"},
                # Deterministic sync: a solicitation can only fire after the
                # monitor has observed a STABLE limit-bearing frame (>=2
                # ticks), so by the time this matches, the latch observation
                # has already been persisted — and the solicitation firing
                # while the limit text is on screen is the incident's physics.
                {"await_keys": {"expect": SOLICITATION_MARKER, "timeout_s": 30}},
                # Scroll the limit text out of the tail-30 marker window
                # (bottom-anchor physics: 301c6489) and settle on a bare
                # idle prompt for the remaining solicitation + exhaustion.
                {"scroll": 60},
                {"render": "F-idle-claude"},
                # No report_result, ever: the script parks and the bounded
                # solicitation loop runs out against the clean tail.
            ],
        )
        scenario.launch_m2(prompt=PROMPT, expected_result={"payload_schema": RESULT_SCHEMA})

        status = _poll_status(harness, scenario.session_id, "failed", 30.0)
        assert status == "failed", f"status={status}\n{harness.log_text()}"

        row = harness.session_row(scenario.session_id)
        # The scenario's premise: no limit text near the terminal-time tail.
        assert LIMIT_TEXT not in (row["output_snippet"] or ""), row["output_snippet"]
        # The durable latch is the wire-independent witness of the mid-attempt
        # blocked_api observation.
        assert row["api_limit_observed_at"] is not None, row
        # The base turn-end-without-result reason stays verbatim (S3).
        assert "after 2 solicitation(s)" in (row["last_validation_error"] or ""), row[
            "last_validation_error"
        ]
        cause = json.loads(row["terminal_cause_json"])
        assert cause["category"] == "rate_limited", cause
        assert cause["retryable"] is True, cause
        # blocked_api was really observed mid-attempt (S8a level-trigger).
        assert any(
            e["event_type"] == "session_blocked" for e in harness.events(scenario.session_id)
        ), harness.events(scenario.session_id)


@pytest.mark.skipif(
    not HY_GATE,
    reason=(
        "S8d asserts the issue #557 durable api-limit latch on the canonical "
        "Hy host; the retired Rust implementation predates it"
    ),
)
def test_s8d_failure_after_limit_scrolled_out_maps_rate_limited() -> None:
    """S8b with the racy tail made explicit: the failure marker lands AFTER
    the limit text has scrolled out of the tail-30 window — the reason-less
    failed-output mapping must consult the durable latch, not just the
    terminal-time observation."""
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s8d",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"render": "F-api-limit"},
                # Keep the pane ACTIVE while the limit text is on screen so
                # turn-end (and its solicitation budget) never arms during
                # the observation window.
                {"render": "F-active-claude"},
                # >=2s of a static frame at the 100ms monitor tick: the
                # limit-bearing frame is deterministically observed (and the
                # latch persisted) before it scrolls out.
                {"sleep_s": 2.0},
                {"scroll": 60},
                {"render": "F-failed"},
            ],
        )
        scenario.launch_m2(prompt=PROMPT, expected_result={"payload_schema": RESULT_SCHEMA})

        status = _poll_status(harness, scenario.session_id, "failed", 30.0)
        assert status == "failed", f"status={status}\n{harness.log_text()}"

        row = harness.session_row(scenario.session_id)
        assert LIMIT_TEXT not in (row["output_snippet"] or ""), row["output_snippet"]
        assert row["api_limit_observed_at"] is not None, row
        cause = json.loads(row["terminal_cause_json"])
        assert cause["category"] == "rate_limited", cause
        assert cause["retryable"] is True, cause
