"""S7 failure taxonomy (contract README S7, tag P, mode M2).

Three independent scenarios, each rendering one of the frozen failure
markers after the agent receives its prompt, then parking. The Rust
monitor classifies on the tail-10-lines substring match
(`output_has_failure_marker`, main.rs:2956) and maps to a
TerminalCauseCategory (`set_failed_output_cause_if_absent`, main.rs:2872)
exactly per the README's TerminalCause freeze table:

  F-failed-auth ("authentication failed")  -> runner_unavailable, retryable=false
  F-failed-timeout ("...timed out")        -> timed_out,          retryable=true
  F-failed (generic "fatal error: ...")    -> run_failed,          retryable=false

Confirmed empirically: the failure marker short-circuits
`observed_status_for_snapshot` before the turn-end/result-contract block
even runs, so the session goes terminal on the very next monitor tick
(~0.3s observed) -- no report_result, no idle prompt, and no solicitation
loop ever engages for this path. `last_validation_error` stays NULL for
this path (only `terminal_cause` is populated); the README's checklist
only requires `terminal_cause`, so this test asserts that field via the
(read-only, wire-invisible) session row, matching the S1 template's
`--` convention for obligations not observable over the RPC wire.

Also cleans up the tmux session itself after each variant: a `failed`
RunToCompletion session is auto-killed by the monitor
(`should_cleanup_after_observed_status`, main.rs:2838), so there is
nothing left for the harness teardown to reap, but calling
`AgentdHarness()` fresh per variant keeps each classification fully
isolated.

Depends on `conformance_agent.py`'s bottom-anchor fix (30 newlines printed
at `main()` startup before any frame): the failure classifier only reads
the last 10 lines of the tmux capture, and a pane still printing from the
top can leave that tail-10 window blank, silently missing the marker.
This scenario's own prompt-paste text was long enough that the marker
landed in-window even before that fix (confirmed empirically); the fix
makes it robust regardless of prompt length.
"""

import json

from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."

# (frame, expected TerminalCauseCategory, expected retryable)
VARIANTS = [
    ("F-failed-auth", "runner_unavailable", False),
    ("F-failed-timeout", "timed_out", True),
    ("F-failed", "run_failed", False),
]


def test_s7_failure_taxonomy() -> None:
    for frame, expected_category, expected_retryable in VARIANTS:
        with AgentdHarness(extra_serve_args=["--prompt-judge-cmd", ""]) as harness:
            scenario = harness.scenario(
                f"s7-{frame.lower()}",
                [
                    {"render": "F-idle-claude"},
                    {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                    {"render": frame},
                ],
            )
            scenario.launch_m2(
                prompt=PROMPT,
                expected_result={"payload_schema": RESULT_SCHEMA},
            )
            outcome = harness.client.await_result(scenario.session_id, timeout_seconds=20.0)

            # wire: terminal-without-result, no payload
            assert outcome.result is None, (
                f"[{frame}] unexpected result: {outcome.result!r}\n{harness.log_text()}"
            )

            # row: status failed (observed; the README allows "failed or
            # exited" here, but the marker path always lands on failed)
            # and cause classified exactly per the freeze table
            row = harness.session_row(scenario.session_id)
            assert row["status"] == "failed", (
                f"[{frame}] status={row['status']}\n" + harness.log_text()
            )
            cause = json.loads(row["terminal_cause_json"])
            assert cause["category"] == expected_category, f"[{frame}] {cause}"
            assert cause["retryable"] is expected_retryable, f"[{frame}] {cause}"
