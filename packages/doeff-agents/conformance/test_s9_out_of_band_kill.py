"""S9 out-of-band tmux kill (contract README S9, tag P, mode M2).

The driver kills the session's tmux pane directly (bypassing agentd's own
`session.cancel`/cleanup), simulating an external process death. The next
monitor tick observes `tmux has-session` failing and takes the
RESULT-FIRST branch (main.rs:3922-3951):

  (a) a valid result was already reported before the kill -> `done`,
      payload persisted (the pane's death does not race a landed result)
  (b) no result was ever reported -> `exited`, cause `Lost`,
      retryable=true ("tmux session disappeared")

Both variants avoid the turn-end branch entirely (no `render` of the
turn-activity marker, no idle-after-prompt re-render), so
`--prompt-judge-cmd ""` is not load-bearing here, but it is set anyway for
consistency with the rest of this suite and to keep the run fully
deterministic regardless of future script changes.
"""

import json
import time

from harness import RESULT_SCHEMA, AgentdHarness, kill_session_out_of_band

PROMPT = "Produce the conformance structured result."
PAYLOAD = {"summary": "reported before kill", "ok": True}


def _kill_pane(session_id: str) -> None:
    # Backend-aware (tmux kill-session / herdr pane.close) — harness owns
    # the physics so S9 stays a pure fault-injection scenario.
    kill_session_out_of_band(session_id)


def test_s9a_result_first_survives_kill() -> None:
    with AgentdHarness(extra_serve_args=["--prompt-judge-cmd", ""]) as harness:
        scenario = harness.scenario(
            "s9a",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"report_result": {"payload": PAYLOAD}},
                {"sleep_s": 60},
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )

        # Wait for the report to be ACKed before killing the pane out of
        # band -- the ordering under test is "result landed, then the
        # process died", not a race between the two.
        report_entries = _await_journal_event(scenario, "report_result", timeout_s=20.0)
        assert report_entries, f"report_result never landed\n{harness.log_text()}"

        _kill_pane(scenario.session_id)
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=20.0)

        assert outcome.result == PAYLOAD, (
            f"result-first must survive the out-of-band kill: {outcome.result!r}\n"
            + harness.log_text()
        )
        row = harness.session_row(scenario.session_id)
        assert row["status"] == "done", f"status={row['status']}\n" + harness.log_text()
        assert json.loads(row["result_payload_json"]) == PAYLOAD


def test_s9b_unreported_kill_is_lost() -> None:
    with AgentdHarness(extra_serve_args=["--prompt-judge-cmd", ""]) as harness:
        scenario = harness.scenario(
            "s9b",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"sleep_s": 60},
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )

        # Wait for the launch prompt to actually land before killing --
        # otherwise this could race the launch RPC itself.
        prompt_entries = _await_journal_event(scenario, "keys", timeout_s=20.0)
        assert prompt_entries, f"prompt paste never landed\n{harness.log_text()}"

        _kill_pane(scenario.session_id)
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=20.0)

        assert outcome.result is None, (
            f"no result was ever reported: {outcome.result!r}\n" + harness.log_text()
        )
        row = harness.session_row(scenario.session_id)
        assert row["status"] == "exited", f"status={row['status']}\n" + harness.log_text()
        cause = json.loads(row["terminal_cause_json"])
        assert cause["category"] == "lost", cause
        assert cause["retryable"] is True, cause


def _await_journal_event(scenario, event_name: str, *, timeout_s: float) -> list[dict]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        entries = [e for e in scenario.journal() if e.get("event") == event_name]
        if entries:
            return entries
        time.sleep(0.1)
    return []
