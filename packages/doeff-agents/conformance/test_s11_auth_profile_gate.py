"""S11 auth-profile gate (contract README S11, tag P, mode M1; DOE-003).

(a) codex + no CODEX_HOME anywhere in the launch params: `session.launch`
    must return an error BEFORE creating anything in tmux
    (main.rs:1690-1705 — the gate sits ahead of run_pre_launch_setup and
    tmux_new_session; the only earlier tmux call is the read-only
    `has-session` duplicate-name probe). Observables: the RPC fails, no
    tmux session exists for the id, no session row, and the M1 shim never
    ran (no journal file).

(b) claude + no CLAUDE_CONFIG_DIR: staged enforcement (DOE-003 R3) — the
    launch SUCCEEDS and the daemon logs the R3 WARNING. The daemon
    process gets a scratch CLAUDE_CONFIG_DIR via harness.extra_env so the
    trust writer's daemon-env fallback (main.rs:1500) lands in a test
    dir, never the operator's real ~/.claude.
"""

import subprocess
import time

import pytest
from doeff_agents.agentd_client import AgentdClientError
from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."


def test_s11a_codex_without_codex_home_is_rejected_before_tmux() -> None:
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s11a",
            [
                {"render": "F-idle-codex"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
            ],
        )
        with pytest.raises(AgentdClientError) as excinfo:
            # M1 codex launch with NO CODEX_HOME in session_env (and no
            # command override that could carry a CODEX_HOME= prefix)
            scenario.launch_m1(
                agent_type="codex",
                prompt=PROMPT,
                expected_result={"payload_schema": RESULT_SCHEMA},
            )
        assert "no agent auth profile" in str(excinfo.value), excinfo.value

        # no tmux session was ever created for the id
        probe = subprocess.run(
            ["tmux", "has-session", "-t", scenario.session_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        assert probe.returncode != 0, "rejected launch must leave no tmux session"

        # no session row was registered
        assert harness.client.get_session(scenario.session_id) is None

        # the M1 shim never executed: the journal file was never created
        assert not scenario.journal_path.exists(), scenario.journal()


def test_s11b_claude_without_config_dir_warns_only(tmp_path) -> None:
    daemon_claude_dir = tmp_path / "daemon-claude-config"
    with AgentdHarness(
        extra_env={"CLAUDE_CONFIG_DIR": str(daemon_claude_dir)}
    ) as harness:
        scenario = harness.scenario(
            "s11b",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
            ],
        )
        # launch succeeds despite the missing session-level CLAUDE_CONFIG_DIR
        scenario.launch_m1(
            agent_type="claude",
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )

        log = harness.log_text()
        assert "launched without an explicit" in log and "CLAUDE_CONFIG_DIR" in log, log
        assert scenario.session_id in log, log

        # the warning path still pre-seeds trust — into the daemon-env
        # fallback dir (observed physics of main.rs:1497-1501)
        assert (daemon_claude_dir / ".claude.json").exists(), harness.log_text()

        # the session itself is healthy: the M1 fake got the prompt
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if any(e["event"] == "keys" and e["matched"] for e in scenario.journal()):
                break
            time.sleep(0.2)
        assert any(
            e["event"] == "keys" and e["matched"] for e in scenario.journal()
        ), f"prompt paste never reached the M1 fake\n{harness.log_text()}"
