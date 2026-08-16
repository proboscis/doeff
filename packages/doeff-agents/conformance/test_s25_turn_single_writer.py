"""S25 turn stamps (koine session surface v0, tag P — hy gate only).

Contract (semantics-v0.md operations + docs/turn-stamp-path.md):
turn-open / turn-close are stamped by the SEAT ITSELF (per-source single
writer) against the sessionhost socket. The seat does not know its
session id — the RPC carries a descriptor {pane_id, agent_name,
conversation_id} and the sessionhost resolves it to the adopted row.
An unadopted stamp is an HONEST no-op: reply {"adopted": false}, bump a
visible counter, never an error, never a silent drop. `wait` is stored
opaquely (the parse authority stays with the seat's wait protocol — no
second parser here).

波 1-S1 / ADR-DOE-AGENTS-007 改訂 resolution keys (law
stamp-never-crosses-identities): pane_id first key (a descriptor that
also carries conversation_id never lands on a row anchored to a
DIFFERENT conversation; NULL-conversation rows stay reachable — pre-S1
registrations), conversation_id second key (identity key — reaches the
conversation's newest non-terminal row even while the new pane is not
yet re-adopted), agent_name third key ONLY when the descriptor carries
NEITHER pane_id NOR conversation_id. The 2026-08-17 absorption incident
(a dead adopted row swallowed the live seat's stamps via the name key)
is structurally impossible: an identity-bearing stamp that misses its
identity keys is an honest unadopted no-op, never a foreign-row write.

Holder semantics (fixed 2026-07-21): turn_open -> holder="agent"
(the seat is self-driving), turn_close -> holder=wait.who (the turn
passed to user/work). turn_wait_json carries the wait verbatim.
"""

import json
import uuid

from harness import AgentdHarness


def _adopt(
    harness: AgentdHarness,
    name: str,
    pane_ref: str,
    conversation_id: str | None = None,
) -> str:
    params = {
        "session_name": name,
        "substrate": {"kind": harness.substrate_kind(), "ref": pane_ref},
        "agent_kind": "claude",
    }
    if conversation_id is not None:
        params["conversation_id"] = conversation_id
    result = harness.client.request("session.adopt", params)
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

        # turn-close resolved via the agent_name key — reachable ONLY from a
        # bare descriptor (neither pane_id nor conversation_id carried; an
        # identity-bearing stamp must never fall back to the name key —
        # 波 1-S1 吸い込み修理). wait stored opaquely, holder = wait.who.
        wait = {"who": "user", "kind": "decide", "reason": "レビュー待ち"}
        closed = harness.client.request(
            "session.turn_close",
            {
                "descriptor": {"agent_name": name},
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


# ---------------------------------------------------------------------------
# 波 1-S1: identity keys + name-key restriction (law stamp-never-crosses-
# identities — 2026-08-17 吸い込み実弾の regression pins)
# ---------------------------------------------------------------------------


def test_s25_conversation_key_resolves_when_pane_unregistered() -> None:
    """S1 ①②: a stamp carrying conversation_id reaches the conversation's
    row even when its pane_id is not (yet) registered — the revive window
    between a seat moving panes and the periodic re-adopt catching up."""
    with AgentdHarness() as harness:
        name = f"s25-conv-{uuid.uuid4().hex[:8]}"
        pane_ref = harness.adopt_fixture_session(name)
        conv = f"conv-{uuid.uuid4()}"
        session_id = _adopt(harness, name, pane_ref, conv)

        opened = harness.client.request(
            "session.turn_open",
            {
                "descriptor": {
                    "pane_id": "%s25-moved-pane",
                    "agent_name": name,
                    "conversation_id": conv,
                }
            },
        )
        assert opened == {"adopted": True, "session_id": session_id}, opened
        row = harness.session_row(session_id)
        assert row["turn_holder"] == "agent", row


def test_s25_identity_bearing_stamp_never_lands_on_foreign_row() -> None:
    """吸い込み regression (実弾 2026-08-17): a registered row with the same
    NAME but a different pane must NOT absorb an identity-bearing stamp.
    The dead integration-lead row advanced turn_since on stamps from the
    live seat — under the revised keys this is an honest unadopted no-op
    and the foreign row's turn columns stay untouched."""
    with AgentdHarness() as harness:
        name = f"s25-absorb-{uuid.uuid4().hex[:8]}"
        pane_ref = harness.adopt_fixture_session(name)
        session_id = _adopt(harness, name, pane_ref)  # conversation-less row

        result = harness.client.request(
            "session.turn_open",
            {"descriptor": {"pane_id": "%s25-fresh-pane", "agent_name": name}},
        )
        assert result == {"adopted": False, "session_id": None}, result
        row = harness.session_row(session_id)
        assert row["turn_holder"] is None, row
        assert row["turn_since"] is None, row
        counters = _counters(harness)
        assert counters["turn_stamp_unadopted"] == 1, counters


def test_s25_pane_key_never_crosses_conversations() -> None:
    """pane key × conversation agreement: a stamp whose conversation_id
    disagrees with the pane row's recorded conversation must not corrupt
    that conversation's record (substrate reuse in flight — the new
    conversation's row is minted by the next re-adopt). A NULL-conversation
    pane row stays reachable (pre-S1 registrations keep receiving stamps
    from conversation-carrying hooks)."""
    with AgentdHarness() as harness:
        name = f"s25-cross-{uuid.uuid4().hex[:8]}"
        pane_ref = harness.adopt_fixture_session(name)
        conv1 = f"conv-{uuid.uuid4()}"
        conv2 = f"conv-{uuid.uuid4()}"
        session_id = _adopt(harness, name, pane_ref, conv1)

        crossed = harness.client.request(
            "session.turn_open",
            {"descriptor": {"pane_id": pane_ref, "conversation_id": conv2}},
        )
        assert crossed == {"adopted": False, "session_id": None}, crossed
        row = harness.session_row(session_id)
        assert row["turn_holder"] is None, row

        agreed = harness.client.request(
            "session.turn_open",
            {"descriptor": {"pane_id": pane_ref, "conversation_id": conv1}},
        )
        assert agreed == {"adopted": True, "session_id": session_id}, agreed

        # NULL-conversation row + conversation-carrying stamp → pane key lands
        bare_name = f"s25-null-{uuid.uuid4().hex[:8]}"
        bare_ref = harness.adopt_fixture_session(bare_name)
        bare_sid = _adopt(harness, bare_name, bare_ref)
        landed = harness.client.request(
            "session.turn_open",
            {
                "descriptor": {
                    "pane_id": bare_ref,
                    "conversation_id": f"conv-{uuid.uuid4()}",
                }
            },
        )
        assert landed == {"adopted": True, "session_id": bare_sid}, landed
