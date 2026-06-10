from __future__ import annotations

from doeff_conductor.replay_keying import (
    ResolvedIdentity,
    agent_cache_key,
    longest_valid_prefix,
    node_identity_fingerprint,
    resolved_identity_fingerprint,
)

SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "required": ["status"],
}


def test_agent_cache_key_excludes_substrate() -> None:
    identity = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company")

    tmux_key = agent_cache_key(
        prompt="Implement the feature",
        schema=SCHEMA,
        resolved_identity=identity,
        substrate="tmux",
    )
    headless_key = agent_cache_key(
        prompt="Implement the feature",
        schema=SCHEMA,
        resolved_identity=identity,
        substrate="headless-ci",
    )

    assert tmux_key == headless_key


def test_agent_cache_key_changes_when_only_effort_differs() -> None:
    """Effort affects the result distribution, so it must vary the cache key
    directly — not only via the fingerprint helper (pins the property against
    future restructuring of the key payload)."""
    xhigh = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company", effort="xhigh")
    low = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company", effort="low")

    xhigh_key = agent_cache_key(prompt="p", schema=SCHEMA, resolved_identity=xhigh)
    low_key = agent_cache_key(prompt="p", schema=SCHEMA, resolved_identity=low)

    assert xhigh_key != low_key


def test_agent_cache_key_changes_when_result_distribution_changes() -> None:
    codex = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company")
    frontier = ResolvedIdentity(adapter="claude", model="opus-5", identity="company")

    base_key = agent_cache_key(
        prompt="Implement the feature",
        schema=SCHEMA,
        resolved_identity=codex,
    )

    assert base_key != agent_cache_key(
        prompt="Implement a different feature",
        schema=SCHEMA,
        resolved_identity=codex,
    )
    assert base_key != agent_cache_key(
        prompt="Implement the feature",
        schema={"type": "object", "required": ["ok"]},
        resolved_identity=codex,
    )
    assert base_key != agent_cache_key(
        prompt="Implement the feature",
        schema=SCHEMA,
        resolved_identity=frontier,
    )


def test_identity_fingerprint_is_canonical() -> None:
    first = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company")
    second = ResolvedIdentity(model="gpt-5", adapter="codex", identity="company")

    assert resolved_identity_fingerprint(first) == resolved_identity_fingerprint(second)


def test_identity_fingerprint_includes_effort() -> None:
    """Effort affects the result distribution, so it enters the fingerprint (ADR D7)."""
    xhigh = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company", effort="xhigh")
    low = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company", effort="low")
    unset = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company")

    assert resolved_identity_fingerprint(xhigh) != resolved_identity_fingerprint(low)
    assert resolved_identity_fingerprint(xhigh) != resolved_identity_fingerprint(unset)


def test_identity_fingerprint_is_stable_for_equal_effort() -> None:
    first = ResolvedIdentity(adapter="codex", model="gpt-5", identity="company", effort="xhigh")
    second = ResolvedIdentity(effort="xhigh", model="gpt-5", adapter="codex", identity="company")

    assert resolved_identity_fingerprint(first) == resolved_identity_fingerprint(second)


def test_node_identity_fingerprint_includes_static_path_and_loop_iteration() -> None:
    base = node_identity_fingerprint(
        workflow_name="wf",
        node_path=("Gate", "loop", "agent"),
        loop_indices=(0,),
    )

    assert base == node_identity_fingerprint(
        workflow_name="wf",
        node_path=("Gate", "loop", "agent"),
        loop_indices=(0,),
    )
    assert base != node_identity_fingerprint(
        workflow_name="wf",
        node_path=("Gate", "loop", "agent"),
        loop_indices=(1,),
    )
    assert base != node_identity_fingerprint(
        workflow_name="wf",
        node_path=("Review", "loop", "agent"),
        loop_indices=(0,),
    )


def test_longest_valid_prefix_stops_at_first_changed_key() -> None:
    previous = ["a", "b", "c", "d"]
    current = ["a", "b", "X", "d"]

    assert longest_valid_prefix(previous, current) == 2
    assert longest_valid_prefix(previous, ["a", "b"]) == 2
