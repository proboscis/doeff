"""S29 externally named seat adopt (koine session surface, herdr backend only).

Contract: `session.adopt` carries the seat name that the herdr agent
registry shows (`herdr agent list` — the authority the dotfiles adopt
reconciler reads), and such seats own NO doeff workspace: their workspace
label is the repo/project name (probe 2026-08-17: own seat = agent name
s-7bfc5d5028, label "doeff"). The herdr substrate must therefore answer
has-session for a name that has no label holder by consulting the agent
registry as an EXISTENCE probe (substrate_herdr.hy
herdr-external-agent-pane-id-io — the one sanctioned agent.get; identity /
attribution / kill of doeff-owned sessions stay label-anchored, ADR-DOE-
AGENTS-004 law herdr-session-identity-is-workspace-label). Otherwise
adopt rejects every live interactive seat (adopt_target_not_found) and
already-adopted rows flip to substrate_present=false wholesale.

Also pins the mirror principle for such seats: when the seat's workspace is
closed out of band, substrate_present flips to false and the row stays
non-terminal (S27 for the external-name shape).
"""

import time
import uuid

import pytest

from harness import AgentdHarness, kill_session_out_of_band

ACTIVE_STATUSES = {"pending", "booting", "running", "blocked", "blocked_api"}


def test_s29_externally_named_seat_is_adoptable_and_present() -> None:
    with AgentdHarness() as harness:
        if harness.substrate_kind() != "herdr":
            pytest.skip("externally named seats exist on the herdr backend only")
        name = f"s29-ext-{uuid.uuid4().hex[:8]}"
        pane_ref = harness.adopt_external_seat_fixture(name)

        adopted = harness.client.request(
            "session.adopt",
            {
                "session_name": name,
                "substrate": {"kind": harness.substrate_kind(), "ref": pane_ref},
                "agent_kind": "claude",
            },
        )
        session_id = adopted["session_id"]
        assert adopted["adopted"] is True, adopted
        assert adopted["substrate_present"] is True, adopted

        live = harness.client.request("session.get", {"session_id": session_id})
        assert live["substrate_present"] is True, live
        assert live["session_name"] == name, live

        # mirror principle for the external-name shape: the seat vanishes
        # out of band -> presence flips, row is neither terminalized nor deleted.
        kill_session_out_of_band(f"{name}-ws")
        deadline = time.monotonic() + 10.0
        wire = harness.client.request("session.get", {"session_id": session_id})
        while time.monotonic() < deadline and wire["substrate_present"] is not False:
            time.sleep(0.3)
            wire = harness.client.request("session.get", {"session_id": session_id})
        assert wire["substrate_present"] is False, wire
        assert wire["status"] in ACTIVE_STATUSES, wire
        row = harness.session_row(session_id)
        assert row["finished_at"] is None, row
