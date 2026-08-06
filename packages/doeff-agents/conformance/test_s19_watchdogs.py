"""S19 watchdogs (contract README S19, tag P, mode M2).

Three independent watchdogs, one scenario each:

  (a) launch-timeout: the pane never shows a startup-finished signal (no
      idle glyph, no active marker, no ⏺), so `observed_active_at` stays
      NULL and the session is failed with cause timed_out retryable=true
      once `max(started_at, observation_gap_at)` is older than
      DOEFF_AGENTD_LAUNCH_TIMEOUT_SECS (ADR-DOE-AGENTS-009 R2: an
      observation gap voids the "watched continuously, never saw active"
      premise and restarts the window). F-frozen renders exactly that
      "live but never started" shape.
  (b) zombie idle shell: the agent process exits and tmux drops the pane
      back to its parent shell; `pane_current_command` reads as a known
      shell -> exited, cause vanished retryable=true (second-evidence
      death — split from lost by ADR-DOE-AGENTS-009 R3).
  (c) out-of-band pane kill with the session alive (contract REVISED by
      issue #568 / ADR-DOE-AGENTS-010 R4 — pane-ownership validation):
      the monitor now verifies every cycle that the recorded pane_id
      still belongs to the row's session (`display-message
      #{session_name}`). A pane that tmux positively reports as gone is
      SECOND EVIDENCE of death (same class as the zombie shell and the
      vanished session — tmux answered), NOT a supply cut: the row is
      terminalized exited + vanished immediately and the terminal sweep
      cleans the surviving tmux session. Under the pre-#568 contract this
      shape simulated a supply cut (capture aborting every tick) and the
      row was held `running` with `observation_gap_at` stamped; that
      hold-and-stamp semantics for GENUINE supply cuts (probe/capture
      raising without a positive answer — ADR-DOE-AGENTS-009 R1) is
      still contract, and is owned by the hy deftest gate
      (sessionhost_policy_deftests: test-stale-observation-holds-and-
      records-gap / test-stale-observation-gap-event-is-rate-bounded),
      because no real-tmux black-box shape can make capture fail while
      the pane demonstrably exists.

All thresholds ride harness.extra_env because they are env-only knobs on
the daemon process.
"""

import json
import time

from harness import RESULT_SCHEMA, AgentdHarness, break_pane_observation_out_of_band

PROMPT = "Produce the conformance structured result."


def _await_row_status(harness, session_id, statuses, *, timeout_s: float):
    deadline = time.monotonic() + timeout_s
    row = harness.session_row(session_id)
    while time.monotonic() < deadline and row["status"] not in statuses:
        time.sleep(0.3)
        row = harness.session_row(session_id)
    return row


def test_s19a_launch_timeout_reaps_never_started_session() -> None:
    with AgentdHarness(
        extra_env={"DOEFF_AGENTD_LAUNCH_TIMEOUT_SECS": "3"}
    ) as harness:
        scenario = harness.scenario(
            "s19a",
            [
                # neither idle glyph nor active marker nor turn activity:
                # startup never visibly finishes, observed_active_at stays
                # NULL, and the pane stays byte-identical (park)
                {"render": "F-frozen"},
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=30.0)
        assert outcome.result is None

        row = _await_row_status(harness, scenario.session_id, ("failed",), timeout_s=10.0)
        assert row["status"] == "failed", (
            f"status={row['status']}\n" + harness.log_text()
        )
        assert "launch timeout" in (row["last_validation_error"] or ""), row[
            "last_validation_error"
        ]
        cause = json.loads(row["terminal_cause_json"])
        assert cause["category"] == "timed_out", cause
        assert cause["retryable"] is True, cause

        types = [e["event_type"] for e in harness.events(scenario.session_id)]
        assert "session_launch_timeout" in types, types


def test_s19b_zombie_idle_shell_is_reaped_as_lost() -> None:
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s19b",
            [
                # exit immediately after startup: the pane returns to its
                # parent shell while the tmux session stays alive
                {"exit": 0},
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=30.0)
        assert outcome.result is None

        row = _await_row_status(harness, scenario.session_id, ("exited",), timeout_s=10.0)
        assert row["status"] == "exited", (
            f"status={row['status']}\n" + harness.log_text()
        )
        cause = json.loads(row["terminal_cause_json"])
        assert cause["category"] == "vanished", cause
        assert cause["retryable"] is True, cause
        assert "idle shell" in (cause.get("reason") or ""), cause

        assert any(e["event"] == "exiting" for e in scenario.journal())
        types = [e["event_type"] for e in harness.events(scenario.session_id)]
        assert "session_exited" in types, types


def test_s19c_pane_gone_while_session_alive_is_evidenced_vanish() -> None:
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s19c",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                # park at the idle prompt: the awaiting_response latch stays
                # set (no active marker is ever rendered), which keeps
                # turn-end/solicitation/stall silent — the session just
                # sits `running` with a fresh last_observed_at every tick
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )

        # wait for at least one successful observation
        deadline = time.monotonic() + 15.0
        row = harness.session_row(scenario.session_id)
        while time.monotonic() < deadline and row["last_observed_at"] is None:
            time.sleep(0.2)
            row = harness.session_row(scenario.session_id)
        assert row["last_observed_at"] is not None, harness.log_text()

        # keep the SESSION-liveness answer alive but kill the monitored PANE:
        # tmux now positively reports the recorded pane as gone. That is
        # second evidence (the observed subject's tty is dead, tmux answered)
        # — the pane-ownership arm (ADR-DOE-AGENTS-010 R4) terminalizes
        # exited + vanished immediately instead of holding unknown, and the
        # terminal sweep (R5) cleans the surviving tmux session.
        break_pane_observation_out_of_band(scenario.session_id, row["pane_id"])

        row = _await_row_status(harness, scenario.session_id, ("exited",), timeout_s=15.0)
        assert row["status"] == "exited", (
            f"status={row['status']}\n" + harness.log_text()
        )
        cause = json.loads(row["terminal_cause_json"])
        assert cause["category"] == "vanished", cause
        assert cause["retryable"] is True, cause
        assert "no longer belongs to session" in (cause.get("reason") or ""), cause

        # never the retired false-lost shape
        types = [e["event_type"] for e in harness.events(scenario.session_id)]
        assert "session_exited" in types, types
        assert "session_stale_reaped" not in types, types

        # the terminal sweep collects the survivor session (cleaned_at is the
        # sweep's witness; the kill is journaled as session_cleaned)
        deadline = time.monotonic() + 15.0
        row = harness.session_row(scenario.session_id)
        while time.monotonic() < deadline and row["cleaned_at"] is None:
            time.sleep(0.3)
            row = harness.session_row(scenario.session_id)
        assert row["cleaned_at"] is not None, harness.log_text()

        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=5.0)
        assert outcome.result is None
