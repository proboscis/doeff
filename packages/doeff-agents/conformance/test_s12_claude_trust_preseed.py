"""S12 claude trust pre-seed (contract README S12, tag P, mode M1; scar
42fb28fa).

A claude M1 launch with `CLAUDE_CONFIG_DIR=<tmp>` in session_env must land
`projects.<canonicalized work_dir>.hasTrustDialogAccepted == true` in
`<tmp>/.claude.json` BEFORE the CLI starts (trust_claude_workspace,
main.rs:1493) — otherwise a fresh workspace stalls the launch on the
interactive trust dialog. Claude keys projects by the REALPATH of the cwd
(`/tmp` is `/private/tmp` on macOS), hence `os.path.realpath` here.

The write is temp+rename (`.claude.json.agentd-tmp` → `.claude.json`,
main.rs:1540) so a concurrent claude never reads a torn state file; the
observable of that discipline is the absence of the leftover tmp file.

Because the trust dialog is pre-seeded, the fake renders the idle REPL
immediately and the launch RPC (which blocks in wait_for_repl_idle until
the glyph appears) returns promptly — this doubles as the M1 smoke assert
that the PATH shim really shadowed `claude`.
"""

from __future__ import annotations

import json
import os
import time

from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."


def test_s12_claude_trust_preseeded_into_config_dir(tmp_path) -> None:
    claude_config_dir = tmp_path / "claude-config"
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s12",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
            ],
        )
        scenario.launch_m1(
            agent_type="claude",
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
            extra_env={"CLAUDE_CONFIG_DIR": str(claude_config_dir)},
        )

        state_path = claude_config_dir / ".claude.json"
        assert state_path.exists(), (
            f"trust pre-seed never wrote {state_path}\n" + harness.log_text()
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        trusted_dir = os.path.realpath(str(scenario.work_dir))
        project = state["projects"][trusted_dir]
        assert project["hasTrustDialogAccepted"] is True, state
        assert project["hasCompletedProjectOnboarding"] is True, state
        # temp+rename discipline: no torn/leftover temp file
        assert not (claude_config_dir / ".claude.json.agentd-tmp").exists()

        # M1 really ran: the PATH shim was executed by the real launch
        # pipeline and the pasted prompt reached the fake's tty
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            entries = scenario.journal()
            if any(e["event"] == "keys" and e["matched"] for e in entries):
                break
            time.sleep(0.2)
        events = [e["event"] for e in scenario.journal()]
        assert "started" in events, events
        assert any(
            e["event"] == "keys" and e["matched"] for e in scenario.journal()
        ), f"prompt paste never reached the M1 fake\n{harness.log_text()}"
