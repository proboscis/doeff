"""S14 identity persistence — EXPECTED-RED on the Rust oracle (contract
README S14, tag **X**, mode M1; DOE-004 contract extension, checklist (b)).

The DOE-004 contract extension requires the RESOLVED effective agent
identity (the CODEX_HOME / CLAUDE_CONFIG_DIR the session actually ran
with) to be persisted on the session row, so an operator can answer
"which auth profile burned this quota?" after the fact. The Rust oracle
does NOT implement this: `agent_sessions` has no identity column and the
effective CODEX_HOME appears nowhere in the row.

Per the suite's X-row discipline (README: X 項目を P として数えて oracle
green を主張することは禁止), this test asserts the CURRENT oracle
behaviour — the ABSENCE — as the recorded expected-red. The Hy
implementation must ADD the column; when it does, this test is replaced
by the positive assert behind the Hy gate (and the absence assert below
starts failing on the Hy side, which is the desired tripwire).

The launch is a real M1 codex launch with an explicit session-level
CODEX_HOME, so the "identity that should have been persisted" is a
concrete, known value — the strongest possible absence assert.
"""

from __future__ import annotations

import json
import time

from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."

# Column-name fragments any reasonable identity persistence would use.
IDENTITY_COLUMN_FRAGMENTS = (
    "codex_home",
    "claude_config",
    "config_dir",
    "auth_profile",
    "identity",
)


def test_s14_expected_red_no_resolved_identity_on_session_row(tmp_path) -> None:
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

        row = harness.session_row(scenario.session_id)

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
        assert str(codex_home) not in row_dump, (
            "the effective CODEX_HOME appeared in the session row — S14 "
            "graduates from expected-red"
        )
