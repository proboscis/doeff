"""S24 adopted ids are opaque (koine session surface v0, tag P — hy gate only).

Contract (semantics-v0.md resource table): `id` is an OPAQUE string —
the sessionhost mints it, callers must never parse it, and it must not
simply be the caller-provided name (importing caller naming conventions
into the id is the banned shape). The only thing an id guarantees is
the roundtrip: session.get(id) resolves the same row.
"""

import uuid

from harness import AgentdHarness


def test_s24_adopted_id_is_opaque_and_roundtrips() -> None:
    with AgentdHarness() as harness:
        name = f"s24-opaque-{uuid.uuid4().hex[:8]}"
        pane_ref = harness.adopt_fixture_session(name)

        result = harness.client.request(
            "session.adopt",
            {
                "session_name": name,
                "substrate": {"kind": harness.substrate_kind(), "ref": pane_ref},
                "agent_kind": "claude",
            },
        )
        session_id = result["session_id"]
        assert isinstance(session_id, str)
        assert session_id

        # opaque: the id is minted by the sessionhost, not the caller's name
        # (and does not embed it — parsing the id must never recover caller
        # vocabulary)
        assert session_id != name
        assert name not in session_id
        assert pane_ref not in session_id

        # roundtrip: get(id) resolves the same row with the same id
        fetched = harness.client.request("session.get", {"session_id": session_id})
        assert fetched is not None
        assert fetched["session_id"] == session_id
        assert fetched["session_name"] == name
        assert fetched["adopted"] is True, fetched
