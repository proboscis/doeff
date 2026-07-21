"""S27 mirror principle (koine safety clause 3, tag P — hy gate only).

Contract (semantics-v0.md safety clause 3): reality (the substrate) is
authoritative; the ledger row is a derived projection. When an adopted
seat's pane vanishes, the ledger does NOT overrule reality with a
verdict (no exited, no delete) — it RECONCILES: the wire view carries
the derived divergence fields `substrate_present: bool` /
`substrate_checked_at: iso` while the row itself stays non-terminal
and undeleted.
"""

import time
import uuid

from harness import AgentdHarness, kill_session_out_of_band

ACTIVE_STATUSES = {"pending", "booting", "running", "blocked", "blocked_api"}


def test_s27_vanished_pane_is_reconciled_not_deleted() -> None:
    with AgentdHarness() as harness:
        name = f"s27-mirror-{uuid.uuid4().hex[:8]}"
        pane_ref = harness.adopt_fixture_session(name)
        adopted = harness.client.request(
            "session.adopt",
            {
                "session_name": name,
                "substrate": {"kind": harness.substrate_kind(), "ref": pane_ref},
                "agent_kind": "claude",
            },
        )
        session_id = adopted["session_id"]

        # while the pane lives, the wire view reconciles presence
        live = harness.client.request("session.get", {"session_id": session_id})
        assert live["substrate_present"] is True, live
        assert isinstance(live["substrate_checked_at"], str), live

        kill_session_out_of_band(name)

        # the wire view flips to absent (reconciliation display) ...
        deadline = time.monotonic() + 10.0
        wire = harness.client.request("session.get", {"session_id": session_id})
        while time.monotonic() < deadline and wire["substrate_present"] is not False:
            time.sleep(0.3)
            wire = harness.client.request("session.get", {"session_id": session_id})
        assert wire["substrate_present"] is False, wire
        assert isinstance(wire["substrate_checked_at"], str), wire

        # ... while the row is neither terminalized nor deleted
        assert wire["status"] in ACTIVE_STATUSES, wire
        row = harness.session_row(session_id)
        assert row["status"] in ACTIVE_STATUSES, row
        assert row["finished_at"] is None, row
        assert row["terminal_cause_json"] is None, row

        # and it still shows up in the adopted-seat ledger view
        listed = harness.client.request("session.list", {"adopted": True})
        assert [r["session_id"] for r in listed] == [session_id], listed
