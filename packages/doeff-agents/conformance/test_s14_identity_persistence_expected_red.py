"""S14 identity persistence — EXPECTED-RED on the Rust oracle (contract
README S14, tag **X**, mode M1; DOE-004 contract extension, checklist (b)).

The DOE-004 contract extension requires the RESOLVED effective agent
identity (the CODEX_HOME / CLAUDE_CONFIG_DIR the session actually ran
with) to be persisted on the session row, so an operator can answer
"which auth profile burned this quota?" after the fact. The Rust oracle
does NOT implement this: `agent_sessions` has no identity column and the
effective CODEX_HOME appears nowhere in the row.

Per the suite's X-row discipline (README: X 項目を P として数えて oracle
green を主張することは禁止), the expected-red test asserts the CURRENT
oracle behaviour — the ABSENCE. The Hy session host (C3) ADDS the column,
so S14 is split per gate (README「2 ゲートの区別」/ the seam env var
`CONFORMANCE_AGENTD_BIN` selects the daemon under test):

- oracle gate (no seam): the expected-red absence assert stays recorded;
  the positive assert is skipped. If the Rust oracle ever grows the
  column, the absence assert fails loudly — the graduation tripwire.
- Hy gate (seam set): the positive assert runs — the resolved effective
  identity (here the explicit session-level CODEX_HOME) must be persisted
  byte-exactly in `effective_identity_json`, with the transient `warnings`
  key stripped. The absence assert is skipped (its failure on the Hy side
  is by design, not information).

The launch is a real M1 codex launch with an explicit session-level
CODEX_HOME, so the identity is a concrete, known value on both gates.
"""

import json
import os
import time

import pytest

from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."

# Transfer-gate seam (harness.build_agentd): set => the Hy host is the
# daemon under test; unset => the Rust oracle.
HY_GATE = bool(os.environ.get("CONFORMANCE_AGENTD_BIN"))

# Column-name fragments any reasonable identity persistence would use.
IDENTITY_COLUMN_FRAGMENTS = (
    "codex_home",
    "claude_config",
    "config_dir",
    "auth_profile",
    "identity",
)


def _launched_session_row(tmp_path) -> tuple[dict, str]:
    """Shared S14 physics: a real M1 codex launch with an explicit
    session-level CODEX_HOME; returns (session row, that CODEX_HOME)."""
    codex_home = tmp_path / "codex-home-s14"
    codex_home.mkdir()
    daemon_codex_home = tmp_path / "daemon-codex-home"
    with AgentdHarness(extra_env={"CODEX_HOME": str(daemon_codex_home)}) as harness:
        scenario = harness.scenario(
            "s14",
            [
                {"render": "F-idle-codex"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"record_env": ["CODEX_HOME"]},
            ],
        )
        scenario.launch_m1(
            agent_type="codex",
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
            extra_env={"CODEX_HOME": str(codex_home)},
        )

        # the fake really ran WITH the identity we are about to look for
        deadline = time.monotonic() + 15.0
        env_entries: list[dict] = []
        while time.monotonic() < deadline and not env_entries:
            env_entries = [e for e in scenario.journal() if e["event"] == "env"]
            time.sleep(0.2)
        assert env_entries and env_entries[0]["values"]["CODEX_HOME"] == str(codex_home)

        return harness.session_row(scenario.session_id), str(codex_home)


@pytest.mark.skipif(
    HY_GATE,
    reason=(
        "oracle-only expected-red: the Hy host implements identity "
        "persistence, so the absence assert failing there is by design — "
        "the Hy gate runs the positive assert below instead"
    ),
)
def test_s14_expected_red_no_resolved_identity_on_session_row(tmp_path) -> None:
    row, codex_home = _launched_session_row(tmp_path)

    # EXPECTED-RED (oracle): no identity column exists ...
    identity_columns = [
        column
        for column in row.keys()
        if any(fragment in column.lower() for fragment in IDENTITY_COLUMN_FRAGMENTS)
    ]
    assert identity_columns == [], (
        "the Rust oracle grew an identity column — S14 graduates from "
        f"expected-red; move it to the positive assert: {identity_columns}"
    )

    # ... and the effective CODEX_HOME leaks through NO column value
    # (backend_ref/command/expected_result/etc. are all serialized here)
    row_dump = json.dumps({k: str(v) for k, v in row.items()})
    assert codex_home not in row_dump, (
        "the effective CODEX_HOME appeared in the session row — S14 "
        "graduates from expected-red"
    )


@pytest.mark.skipif(
    not HY_GATE,
    reason=(
        "positive identity-persistence assert: requires the Hy session "
        "host's effective_identity_json column (DOE-004 contract "
        "extension) — the Rust oracle records the absence above"
    ),
)
def test_s14_identity_persisted_on_session_row(tmp_path) -> None:
    row, codex_home = _launched_session_row(tmp_path)

    # The resolved identity is persisted on the row, byte-exact: for a
    # codex launch that is the effective CODEX_HOME the session ran with.
    raw = row.get("effective_identity_json")
    assert raw, f"effective_identity_json missing/empty on the session row: {row.keys()}"
    identity = json.loads(raw)
    assert identity == {"CODEX_HOME": codex_home}, identity

    # The transient `warnings` key (operator-log side product of
    # PreLaunchSetup) must NOT be persisted as identity.
    assert "warnings" not in identity
