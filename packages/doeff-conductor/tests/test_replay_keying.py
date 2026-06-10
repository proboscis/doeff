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
