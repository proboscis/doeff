"""S23 session.adopt ordering (koine session surface v0, tag P — hy gate only).

Contract (koine surfaces/session/semantics-v0.md, operations):
adopt registers an ALREADY-LIVE seat after the fact, with a mandatory
order: existence check FIRST, registration only on success. A failed
adopt must leave NO row behind (the "phantom turn-open" regression the
ordering clause exists to prevent). Adoption is observation-only
(safety clause 2): the target pane must not be touched.

波 1-S1 / ADR-DOE-AGENTS-007 改訂 (design-conversation-liveness-
sessionhost-wave1-2026-08-17 付録 4-2 S1): adopt carries an optional
`conversation_id` and writes it to conversation_json ({"session_id": …}
— the ADR-DOE-AGENTS-006 union's identity member). The idempotent path
is conversation-aware: exact match returns the row, a NULL-conversation
row is backfilled (registration completion of the SAME incarnation —
COALESCE first-write-wins is the final defence), and a DIFFERENT
conversation on the same substrate mints a NEW incarnation row (pane
reuse = a new conversation's new incarnation; rows are never rewritten
to point at another conversation). `session.by_conversation` is the
probe-free row lookup: conversation_id → newest non-terminal row, else
newest terminal row, with NO substrate probe (substrate_present is
intentionally ABSENT — absent, not stale).

The Rust oracle does not implement koine; like S20/S21 these scenarios
gate the Hy sessionhost only (CONFORMANCE_AGENTD_BIN).
"""

import json
import uuid

import pytest
from doeff_agents.agentd_client import AgentdClientError
from harness import AgentdHarness

ACTIVE_STATUSES = {"pending", "booting", "running", "blocked", "blocked_api"}


def _adopt_params(
    harness: AgentdHarness,
    name: str,
    ref: str,
    conversation_id: str | None = None,
) -> dict:
    params = {
        "session_name": name,
        "substrate": {"kind": harness.substrate_kind(), "ref": ref},
        "agent_kind": "claude",
    }
    if conversation_id is not None:
        params["conversation_id"] = conversation_id
    return params


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
        assert isinstance(session_id, str), result
        assert session_id, result

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


# ---------------------------------------------------------------------------
# 波 1-S1: conversation_id first-class registration (ADR-DOE-AGENTS-007 改訂)
# ---------------------------------------------------------------------------


def test_s23_adopt_with_conversation_id_registers_conversation() -> None:
    """S1 ①: adopt carries conversation_id → the ledger row carries the
    conversation identity ({"session_id": …} — the ADR-006 union's identity
    member) and the wire response exposes it."""
    with AgentdHarness() as harness:
        name = f"s23-conv-{uuid.uuid4().hex[:8]}"
        pane_ref = harness.adopt_fixture_session(name)
        conv = f"conv-{uuid.uuid4()}"

        result = harness.client.request(
            "session.adopt", _adopt_params(harness, name, pane_ref, conv)
        )
        assert result["conversation"] == {"session_id": conv}, result

        row = harness.session_row(result["session_id"])
        assert row["conversation_json"] is not None, row
        assert json.loads(row["conversation_json"]) == {"session_id": conv}, row


def test_s23_adopt_backfills_conversation_on_existing_row() -> None:
    """S1 ①: the idempotent path completes the registration of the SAME
    incarnation — a NULL-conversation row gains the conversation identity on
    re-adopt (COALESCE first-write-wins in the store is the final defence;
    the row is NOT duplicated)."""
    with AgentdHarness() as harness:
        name = f"s23-backfill-{uuid.uuid4().hex[:8]}"
        pane_ref = harness.adopt_fixture_session(name)

        bare = harness.client.request(
            "session.adopt", _adopt_params(harness, name, pane_ref)
        )
        sid = bare["session_id"]
        assert harness.session_row(sid)["conversation_json"] is None

        conv = f"conv-{uuid.uuid4()}"
        filled = harness.client.request(
            "session.adopt", _adopt_params(harness, name, pane_ref, conv)
        )
        assert filled["session_id"] == sid, filled
        assert filled["conversation"] == {"session_id": conv}, filled
        row = harness.session_row(sid)
        assert json.loads(row["conversation_json"]) == {"session_id": conv}, row
        assert len(harness.session_rows_by_name(name)) == 1

        # idempotent within the same conversation: no new row, same identity
        again = harness.client.request(
            "session.adopt", _adopt_params(harness, name, pane_ref, conv)
        )
        assert again["session_id"] == sid, again
        assert len(harness.session_rows_by_name(name)) == 1


def test_s23_adopt_conversation_change_mints_new_incarnation_row() -> None:
    """S1 ①: substrate reuse by a DIFFERENT conversation (e.g. /clear mints a
    new conversation in the same pane) is a NEW incarnation → a NEW row. The
    old row is never rewritten to point at another conversation (its
    conversation identity is frozen — first-write-wins) and is never
    terminalized by adopt (interactive rows have no terminal writer)."""
    with AgentdHarness() as harness:
        name = f"s23-reuse-{uuid.uuid4().hex[:8]}"
        pane_ref = harness.adopt_fixture_session(name)
        conv1 = f"conv-{uuid.uuid4()}"
        conv2 = f"conv-{uuid.uuid4()}"

        first = harness.client.request(
            "session.adopt", _adopt_params(harness, name, pane_ref, conv1)
        )
        second = harness.client.request(
            "session.adopt", _adopt_params(harness, name, pane_ref, conv2)
        )
        assert second["session_id"] != first["session_id"], (first, second)
        assert second["conversation"] == {"session_id": conv2}, second

        rows = {r["session_id"]: r for r in harness.session_rows_by_name(name)}
        assert len(rows) == 2, rows
        old = rows[first["session_id"]]
        assert json.loads(old["conversation_json"]) == {"session_id": conv1}, old
        assert old["status"] in ACTIVE_STATUSES, old

        # idempotent within the new conversation: no third row
        again = harness.client.request(
            "session.adopt", _adopt_params(harness, name, pane_ref, conv2)
        )
        assert again["session_id"] == second["session_id"], again
        assert len(harness.session_rows_by_name(name)) == 2


def test_s23_session_by_conversation_is_probe_free_lookup() -> None:
    """S1 ②: conversation_id → row の行引き口. Returns the newest matching
    non-terminal row WITHOUT any substrate probe — substrate_present /
    substrate_checked_at are intentionally ABSENT (absent, not stale: the
    reader conjoins its own substrate observation). Unknown conversation
    → null. After substrate reuse each conversation resolves to its own
    incarnation row."""
    with AgentdHarness() as harness:
        name = f"s23-lookup-{uuid.uuid4().hex[:8]}"
        pane_ref = harness.adopt_fixture_session(name)
        conv1 = f"conv-{uuid.uuid4()}"
        conv2 = f"conv-{uuid.uuid4()}"

        first = harness.client.request(
            "session.adopt", _adopt_params(harness, name, pane_ref, conv1)
        )
        looked = harness.client.request(
            "session.by_conversation", {"conversation_id": conv1}
        )
        assert looked is not None
        assert looked["session_id"] == first["session_id"], looked
        assert looked["conversation"] == {"session_id": conv1}, looked
        assert "substrate_present" not in looked, looked
        assert "substrate_checked_at" not in looked, looked
        assert looked["stalled"] is False, looked

        missing = harness.client.request(
            "session.by_conversation", {"conversation_id": f"conv-{uuid.uuid4()}"}
        )
        assert missing is None, missing

        second = harness.client.request(
            "session.adopt", _adopt_params(harness, name, pane_ref, conv2)
        )
        for conv, expected in ((conv1, first), (conv2, second)):
            looked = harness.client.request(
                "session.by_conversation", {"conversation_id": conv}
            )
            assert looked["session_id"] == expected["session_id"], (conv, looked)
