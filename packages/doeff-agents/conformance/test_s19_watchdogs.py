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
  (c) stale observation (ADR-DOE-AGENTS-009 R1 — 2026-07-28 contract
      revision, root fix for the 2026-07-27 sessionhost wedge false-lost
      incident): `last_observed_at` stops advancing while the session
      stays `running`. An observation gap is a proposition about the
      OBSERVATION SUPPLY, not about the observed agent's death — the
      watchdog no longer terminalizes. It stamps `observation_gap_at`,
      records one `session_observation_gap` event (re-armed by the gap
      stamp itself, so the journal rate is bounded at 1/stale-secs), and
      falls through to the second-evidence arms (tmux liveness probe /
      zombie reaper). Black-box shape: the tmux SESSION stays alive (the
      driver adds a second window) but the monitored PANE is killed out
      of band -> every tick aborts at `tmux_capture`, no second evidence
      is obtainable, and the row is HELD at `running` (unknown;
      boundedness is owned by the engine-side deadman gate, ACP ADR
      0059). The 300s constant has no CLI flag; the suite drives it
      through the semantics-preserving DOEFF_AGENTD_STALE_OBSERVATION_SECS
      env knob (README knob table).

All three thresholds ride harness.extra_env because they are env-only
knobs on the daemon process.
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


def test_s19c_stale_observation_holds_unknown_and_records_gap() -> None:
    with AgentdHarness(
        extra_env={"DOEFF_AGENTD_STALE_OBSERVATION_SECS": "2"}
    ) as harness:
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
        # capture starts failing, last_observed_at freezes, and the gap is
        # detected — but a supply cut is NOT death evidence (ADR-DOE-AGENTS-009
        # R1): the row must be HELD at `running` with the gap stamped, not
        # terminalized as lost.
        break_pane_observation_out_of_band(scenario.session_id, row["pane_id"])

        # wait until the gap event lands (edge-triggered, bounded journal)
        deadline = time.monotonic() + 20.0
        types: list[str] = []
        while time.monotonic() < deadline:
            types = [e["event_type"] for e in harness.events(scenario.session_id)]
            if "session_observation_gap" in types:
                break
            time.sleep(0.3)
        assert "session_observation_gap" in types, (
            f"events={types}\n" + harness.log_text()
        )
        assert "session_stale_reaped" not in types, types

        # unknown is held: no terminal verdict without second evidence
        row = harness.session_row(scenario.session_id)
        assert row["status"] == "running", (
            f"status={row['status']}\n" + harness.log_text()
        )
        assert row["terminal_cause_json"] is None, row["terminal_cause_json"]
        assert row["observation_gap_at"] is not None, row

        # journal rate is bounded: give the monitor a few more ticks and
        # confirm the gap event did not flood (re-armed by the gap stamp —
        # at 2s threshold a flood would show many events within 3s)
        time.sleep(3.0)
        gap_events = [
            e
            for e in harness.events(scenario.session_id)
            if e["event_type"] == "session_observation_gap"
        ]
        assert len(gap_events) <= 3, (
            f"gap events flooded: {len(gap_events)}\n" + harness.log_text()
        )

        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=5.0)
        assert outcome.result is None
