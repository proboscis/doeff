"""S26 interactive seats are never reaped (koine safety clause 1, tag P —
hy gate only. TDD: RED on the pre-fix sessionhost).

Contract (koine semantics-v0.md safety clause 1 / pavo ADR 0003 R4):
reaping is opt-in + fail-closed; `lifecycle=interactive` rows are
UNCONDITIONALLY exempt until the supervision adjudication (stage 3).
The pre-fix sessionhost gates only the pane-kill on run_to_completion —
five monitor arms still TERMINALIZE interactive rows (booting watchdog /
stale reap / launch timeout / mux liveness / zombie reaper), and the
terminal guard (store.hy) then forbids reactivation: the row dies while
the seat lives on as an orphan. These four scenarios pin the reap arms
reachable under the M2 conformance physics (the booting arm needs a
mid-launch daemon death and stays a declared gap).

Each test also asserts `last_observed_at` advanced — the witness that
the monitor actually evaluated the row and CHOSE not to reap (otherwise
a dead monitor would green these tests vacuously).
"""

import time

from doeff_agents.adapters.base import AgentSessionLifecycle
from harness import (
    AgentdHarness,
    break_pane_observation_out_of_band,
    kill_session_out_of_band,
)

ACTIVE_STATUSES = {"pending", "booting", "running", "blocked", "blocked_api"}
PROMPT = "Interactive seat under reap-exemption test."


def _assert_stays_nonterminal(
    harness: AgentdHarness, session_id: str, *, window_s: float
) -> dict:
    """Poll the row for window_s; fail the moment it turns terminal."""
    deadline = time.monotonic() + window_s
    row = harness.session_row(session_id)
    while time.monotonic() < deadline:
        row = harness.session_row(session_id)
        assert row["status"] in ACTIVE_STATUSES, (
            f"interactive row was terminalized: status={row['status']} "
            f"cause={row['terminal_cause_json']}\n" + harness.log_text()
        )
        time.sleep(0.2)
    assert row["finished_at"] is None, row
    assert row["terminal_cause_json"] is None, row
    return row


def _wait_for_row(harness: AgentdHarness, session_id: str, *, timeout_s: float = 15.0):
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            return harness.session_row(session_id)
        except AssertionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def _wait_observed(harness: AgentdHarness, session_id: str, *, timeout_s: float = 15.0):
    deadline = time.monotonic() + timeout_s
    row = _wait_for_row(harness, session_id)
    while time.monotonic() < deadline and row["last_observed_at"] is None:
        time.sleep(0.2)
        row = harness.session_row(session_id)
    assert row["last_observed_at"] is not None, (
        "monitor never observed the interactive row\n" + harness.log_text()
    )
    return row


def test_s26_launch_timeout_does_not_reap_interactive() -> None:
    """launch-timeout arm (policy.hy): status=running + observed_active_at
    None + age > knob is EXACTLY the shape of an adopted/interactive seat
    (observation-only registration never sees a startup marker). Pre-fix
    the row is failed ~2s after registration."""
    with AgentdHarness(
        extra_env={"DOEFF_AGENTD_LAUNCH_TIMEOUT_SECS": "2"}
    ) as harness:
        scenario = harness.scenario(
            "s26-launch-timeout",
            [
                # neither idle glyph nor active marker nor turn activity:
                # observed_active_at stays NULL (S19a shape)
                {"render": "F-frozen"},
            ],
        )
        scenario.launch_m2(
            prompt="",
            lifecycle=AgentSessionLifecycle.INTERACTIVE,
        )
        row = _wait_for_row(harness, scenario.session_id)
        assert row["observed_active_at"] is None, row
        # sit well past the 2s knob: the row must stay non-terminal
        row = _assert_stays_nonterminal(harness, scenario.session_id, window_s=5.0)
        assert row["last_observed_at"] is not None, (
            "no observation witness — monitor never evaluated the row\n"
            + harness.log_text()
        )


def test_s26_stale_observation_does_not_reap_interactive() -> None:
    """stale-reap arm: freeze the observation channel (S19c physics — kill
    the monitored pane, keep session liveness true). Pre-fix the row is
    exited/lost once last_observed_at is older than the knob."""
    with AgentdHarness(
        extra_env={"DOEFF_AGENTD_STALE_OBSERVATION_SECS": "2"}
    ) as harness:
        scenario = harness.scenario(
            "s26-stale",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            lifecycle=AgentSessionLifecycle.INTERACTIVE,
        )
        row = _wait_observed(harness, scenario.session_id)
        break_pane_observation_out_of_band(scenario.session_id, row["pane_id"])
        row = _assert_stays_nonterminal(harness, scenario.session_id, window_s=5.0)


def test_s26_zombie_reaper_does_not_reap_interactive() -> None:
    """zombie arm: the pane foreground returns to an idle shell (S19b
    physics). For an adopted interactive seat a shell foreground is a
    NORMAL state, not death. Pre-fix the row is exited/lost within a
    tick."""
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s26-zombie",
            [
                # exit immediately: the pane drops back to its parent shell
                {"exit": 0},
            ],
        )
        scenario.launch_m2(
            prompt="",
            lifecycle=AgentSessionLifecycle.INTERACTIVE,
        )
        _wait_for_row(harness, scenario.session_id)
        row = _assert_stays_nonterminal(harness, scenario.session_id, window_s=4.0)
        assert row["last_observed_at"] is not None, (
            "no observation witness — monitor never evaluated the row\n"
            + harness.log_text()
        )


def test_s26_mux_disappearance_does_not_reap_interactive() -> None:
    """mux-liveness arm: the seat's session is killed out of band. Mirror
    principle (safety clause 3): reality is authoritative and the ledger
    is a projection — the row is reconciled (S27 wire view), NEVER
    terminalized or deleted by the ledger. Pre-fix the row is exited
    (cause lost) within a tick."""
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s26-mux-gone",
            [
                {"render": "F-idle-claude"},
            ],
        )
        scenario.launch_m2(
            prompt="",
            lifecycle=AgentSessionLifecycle.INTERACTIVE,
        )
        _wait_observed(harness, scenario.session_id)
        kill_session_out_of_band(scenario.session_id)
        row = _assert_stays_nonterminal(harness, scenario.session_id, window_s=4.0)
        # the row survives as a ledger mirror of a vanished seat
        assert row["session_id"] == scenario.session_id
