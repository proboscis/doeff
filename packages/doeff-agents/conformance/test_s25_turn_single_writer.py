"""S25 turn stamps (koine session surface v0, tag P — hy gate only).

Contract (semantics-v0.md operations + docs/turn-stamp-path.md):
turn-open / turn-close are stamped by the SEAT ITSELF (per-source single
writer) against the sessionhost socket. The seat does not know its
session id — the RPC carries a descriptor {pane_id, agent_name} and the
sessionhost resolves it to the adopted row (pane_id first key,
agent_name second key). An unadopted stamp is an HONEST no-op: reply
{"adopted": false}, bump a visible counter, never an error, never a
silent drop. `wait` is stored opaquely (the parse authority stays with
the seat's wait protocol — no second parser here).

Holder semantics (fixed 2026-07-21): turn_open -> holder="agent"
(the seat is self-driving), turn_close -> holder=wait.who (the turn
passed to user/work). turn_wait_json carries the wait verbatim.
"""

import json
import uuid

from harness import AgentdHarness


def _adopt(harness: AgentdHarness, name: str, pane_ref: str) -> str:
    result = harness.client.request(
        "session.adopt",
        {
            "session_name": name,
            "substrate": {"kind": harness.substrate_kind(), "ref": pane_ref},
            "agent_kind": "claude",
        },
    )
    return result["session_id"]


def _counters(harness: AgentdHarness) -> dict:
    status = harness.client.request("daemon.status")
    counters = status.get("counters")
    assert isinstance(counters, dict), status
    return counters


def test_s25_turn_stamp_updates_row() -> None:
    with AgentdHarness() as harness:
        name = f"s25-stamp-{uuid.uuid4().hex[:8]}"
        pane_ref = harness.adopt_fixture_session(name)
        session_id = _adopt(harness, name, pane_ref)

        # turn-open resolved via the pane_id first key
        opened = harness.client.request(
            "session.turn_open",
            {"descriptor": {"pane_id": pane_ref, "agent_name": name}},
        )
        assert opened == {"adopted": True, "session_id": session_id}, opened
        row = harness.session_row(session_id)
        assert row["turn_holder"] == "agent", row
        assert row["turn_since"] is not None, row
        assert row["turn_wait_json"] is None, row
        open_since = row["turn_since"]

        # turn-close resolved via the agent_name second key (pane unknown),
        # wait stored opaquely, holder = wait.who
        wait = {"who": "user", "kind": "decide", "reason": "レビュー待ち"}
        closed = harness.client.request(
            "session.turn_close",
            {
                "descriptor": {"pane_id": "%s25-no-such-pane", "agent_name": name},
                "wait": wait,
            },
        )
        assert closed == {"adopted": True, "session_id": session_id}, closed
        row = harness.session_row(session_id)
        assert row["turn_holder"] == "user", row
        assert row["turn_since"] is not None, row
        assert row["turn_since"] != open_since, row
        assert json.loads(row["turn_wait_json"]) == wait, row

        counters = _counters(harness)
        assert counters["turn_stamp_resolved"] == 2, counters
        assert counters["turn_stamp_unadopted"] == 0, counters


def test_s25_unadopted_turn_stamp_is_noop_with_counter() -> None:
    with AgentdHarness() as harness:
        ghost = f"s25-ghost-{uuid.uuid4().hex[:8]}"
        result = harness.client.request(
            "session.turn_close",
            {
                "descriptor": {"pane_id": "%s25-ghost-pane", "agent_name": ghost},
                "wait": {"who": "user", "kind": "review", "reason": "x"},
            },
        )
        # honest no-op: ok envelope (not an error), adopted:false, and the
        # visible counter that doubles as the adopt-coverage instrument
        assert result == {"adopted": False, "session_id": None}, result
        counters = _counters(harness)
        assert counters["turn_stamp_unadopted"] == 1, counters
        assert counters["turn_stamp_resolved"] == 0, counters
        # no row was created by the stamp
        assert harness.session_rows_by_name(ghost) == []
