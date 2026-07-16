"""S22 session registration precedes the ready gate (tag P).

Contract (issue agentd-session-registration-after-ready-gate, 2026-07-17):
registration is bookkeeping independent of TUI readiness. The daemon
persists the BOOTING row immediately after creating the mux session —
BEFORE `wait_for_repl_idle` — so an external monitor never sees a window
where the session physically exists but no record does. Under the old
ordering that window was legitimately up to 120s wide, and a cold start
tripped the mediagen engine's 60s orphan reconciler (observed live
2026-07-14, mediagen ACP r7).

Scenario physics (mode M1, real launch pipeline): the pane renders only
an unknown startup dialog (F-dialog-unknown), so the launch RPC blocks in
its ready wait for the whole (compressed) repl-idle budget. The row must
be observable DURING that window; the out-of-band SQLite read is the
observation channel the suite grants for wire-invisible obligations
(README: payload durability precedent). When the budget runs out the
launch fails closed (S18) and the same row transitions to terminal
`failed` — a lifecycle transition, never a lingering BOOTING row.
"""

import threading
import time

from doeff_agents.agentd_client import AgentdClientError
from harness import RESULT_SCHEMA, AgentdHarness, session_exists_out_of_band

PROMPT = "Produce the conformance structured result."


def test_s22_booting_row_observable_during_ready_wait(tmp_path) -> None:
    with AgentdHarness(
        extra_env={"DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS": "3"}
    ) as harness:
        scenario = harness.scenario(
            "s22-reg",
            [
                # the unknown dialog just sits there — the REPL never idles
                {"render": "F-dialog-unknown"},
            ],
        )
        launch_errors: list[Exception] = []

        def _launch() -> None:
            try:
                scenario.launch_m1(
                    agent_type="claude",
                    prompt=PROMPT,
                    expected_result={"payload_schema": RESULT_SCHEMA},
                    extra_env={"CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config")},
                )
            except AgentdClientError as error:
                launch_errors.append(error)

        launcher = threading.Thread(target=_launch, name="s22-launch-rpc")
        launcher.start()
        try:
            booting_seen_during_launch = False
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and launcher.is_alive():
                try:
                    row = harness.session_row(scenario.session_id)
                except AssertionError:
                    time.sleep(0.05)
                    continue
                assert row["status"] == "booting", (
                    f"the row visible during the ready wait must be BOOTING: {row}"
                )
                booting_seen_during_launch = True
                break
        finally:
            launcher.join(timeout=60.0)
        assert not launcher.is_alive(), (
            f"launch RPC never returned\n{harness.log_text()}"
        )
        assert booting_seen_during_launch, (
            "no session row was observable while the launch sat in its ready "
            "wait — registration is gated behind TUI readiness\n"
            + harness.log_text()
        )

        # budget exhausted: fail-closed launch error (S18 contract) ...
        assert launch_errors, (
            f"a never-ready launch must fail closed\n{harness.log_text()}"
        )
        assert "did not become ready" in str(launch_errors[0]), launch_errors[0]

        # ... and the SAME row transitioned to terminal failed — no BOOTING
        # row lingers, the mux session is cleaned up
        row = harness.session_row(scenario.session_id)
        assert row["status"] == "failed", row
        assert row["finished_at"] is not None, row
        assert row["terminal_cause_json"] is not None, row
        assert "timed_out" in row["terminal_cause_json"], row
        assert not session_exists_out_of_band(scenario.session_id), (
            f"failed launch leaked its mux session: {scenario.session_id}"
        )
