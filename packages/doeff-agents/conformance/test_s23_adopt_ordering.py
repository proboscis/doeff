"""S23 session.adopt ordering (koine session surface v0, tag P — hy gate only).

Contract (koine surfaces/session/semantics-v0.md, operations):
adopt registers an ALREADY-LIVE seat after the fact, with a mandatory
order: existence check FIRST, registration only on success. A failed
adopt must leave NO row behind (the "phantom turn-open" regression the
ordering clause exists to prevent). Adoption is observation-only
(safety clause 2): the target pane must not be touched.

The Rust oracle does not implement koine; like S20/S21 these scenarios
gate the Hy sessionhost only (CONFORMANCE_AGENTD_BIN).
"""

import uuid

import pytest
from harness import AgentdHarness
from doeff_agents.agentd_client import AgentdClientError

ACTIVE_STATUSES = {"pending", "booting", "running", "blocked", "blocked_api"}


def _adopt_params(harness: AgentdHarness, name: str, ref: str) -> dict:
    return {
        "session_name": name,
        "substrate": {"kind": harness.substrate_kind(), "ref": ref},
        "agent_kind": "claude",
    }


def test_s23_adopt_missing_target_leaves_no_row() -> None:
    with AgentdHarness() as harness:
        name = f"s23-missing-{uuid.uuid4().hex[:8]}"
        with pytest.raises(AgentdClientError) as excinfo:
            harness.client.request(
                "session.adopt",
                _adopt_params(harness, name, "%no-such-pane"),
            )
        # typed wire error, and the ordering obligation: no row was created
        assert excinfo.value.error_code == "adopt_target_not_found", (
            f"error_code={excinfo.value.error_code!r} error={excinfo.value}"
        )
        assert harness.session_rows_by_name(name) == [], (
            "a failed adopt must not register a row (existence check "
            "precedes registration)"
        )


def test_s23_adopt_live_pane_registers_adopted_row() -> None:
    with AgentdHarness() as harness:
        name = f"s23-live-{uuid.uuid4().hex[:8]}"
        pane_ref = harness.adopt_fixture_session(name)

        result = harness.client.request(
            "session.adopt", _adopt_params(harness, name, pane_ref)
        )
        assert result["adopted"] is True, result
        assert result["lifecycle"] == "interactive", result
        assert result["status"] in ACTIVE_STATUSES, result
        session_id = result["session_id"]
        assert isinstance(session_id, str) and session_id, result

        row = harness.session_row(session_id)
        assert row["adopted"] == 1, row
        assert row["lifecycle"] == "interactive", row
        assert row["status"] in ACTIVE_STATUSES, row

        # idempotent: a second adopt of the same substrate.ref returns the
        # existing row instead of registering a duplicate
        again = harness.client.request(
            "session.adopt", _adopt_params(harness, name, pane_ref)
        )
        assert again["session_id"] == session_id, again
        assert len(harness.session_rows_by_name(name)) == 1


def test_s23_session_list_adopted_filter() -> None:
    """Verification 8b: `session.list {"adopted": true}` returns adopted
    seats only — the primary filter for the "who is running right now"
    interactive-seat view."""
    with AgentdHarness() as harness:
        name = f"s23-filter-{uuid.uuid4().hex[:8]}"
        pane_ref = harness.adopt_fixture_session(name)
        adopted = harness.client.request(
            "session.adopt", _adopt_params(harness, name, pane_ref)
        )

        scenario = harness.scenario("s23-filter-rtc", [{"render": "F-idle-claude"}])
        scenario.launch_m2(prompt="", expected_result=None)

        listed = harness.client.request("session.list", {"adopted": True})
        assert [row["session_id"] for row in listed] == [adopted["session_id"]], listed

        unadopted = harness.client.request("session.list", {"adopted": False})
        unadopted_ids = {row["session_id"] for row in unadopted}
        assert adopted["session_id"] not in unadopted_ids, unadopted_ids
        assert scenario.session_id in unadopted_ids, unadopted_ids

        interactive = harness.client.request(
            "session.list", {"lifecycle": "interactive"}
        )
        interactive_ids = {row["session_id"] for row in interactive}
        assert adopted["session_id"] in interactive_ids, interactive_ids
        assert scenario.session_id not in interactive_ids, interactive_ids
