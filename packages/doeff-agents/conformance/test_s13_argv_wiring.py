"""S13 argv wiring (contract README S13, tag P, mode M1; scars 49b3549b +
ADR 0035).

M1 launches make agentd's REAL argv builders run and the fake CLI journal
its `sys.argv` at startup, so the suite can assert the exact flags a real
codex/claude would have received:

  claude (build_claude_argv, main.rs:1405-1465):
    --dangerously-skip-permissions, --settings {"disableAllHooks":true},
    --mcp-config <json wiring the agentd-owned doeff_result stdio server>,
    --strict-mcp-config
  codex (build_codex_argv, main.rs:1345-1403):
    --yolo, -c mcp_servers."doeff_result".command="<agentd bin>",
    -c mcp_servers."doeff_result".args=["report-result-mcp", ...]

The shim passes "$@", so the journal argv[1:] equals build_*_argv()[1:]
(argv[0] is the shim's python script, standing in for the binary name).

The claude variant also runs the FULL M1 golden path (prompt paste →
report_result → done) — the one place the suite proves the launch
pipeline + result channel work end to end in M1, not just per-flag.
"""

import json
import time

from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."
PAYLOAD = {"summary": "m1 golden", "ok": True}


def _started_argv(scenario, *, timeout_s: float = 20.0) -> list[str]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for entry in scenario.journal():
            if entry["event"] == "started":
                return list(entry["argv"])
        time.sleep(0.2)
    raise AssertionError("no 'started' journal entry — the M1 shim never ran")


def test_s13_claude_argv_wiring_and_m1_golden_path(tmp_path) -> None:
    claude_config_dir = tmp_path / "claude-config"
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s13-claude",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"render": "F-turn-activity-claude"},
                {"report_result": {"payload": PAYLOAD}},
                {"render": "F-idle-claude"},
            ],
        )
        scenario.launch_m1(
            agent_type="claude",
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
            extra_env={"CLAUDE_CONFIG_DIR": str(claude_config_dir)},
        )

        args = _started_argv(scenario)
        assert "--dangerously-skip-permissions" in args, args
        settings_index = args.index("--settings")
        assert args[settings_index + 1] == '{"disableAllHooks":true}', args
        assert "--strict-mcp-config" in args, args

        mcp_index = args.index("--mcp-config")
        mcp_config = json.loads(args[mcp_index + 1])
        server = mcp_config["mcpServers"]["doeff_result"]
        assert server["type"] == "stdio", server
        # The daemon wires ITSELF as the report-result-mcp server (oracle
        # agentd_binary_path = current_exe) — compare against the binary
        # under test, not a hardcoded name, so the CONFORMANCE_AGENTD_BIN
        # seam exercises the same contract.
        assert server["command"] == str(harness.agentd_bin), server
        assert server["args"] == [
            "report-result-mcp",
            "--session",
            scenario.session_id,
            "--socket",
            str(harness.socket_path),
        ], server

        # full M1 golden path: the wired channel delivers a byte-faithful
        # payload and the session lands done
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=30.0)
        assert outcome.result == PAYLOAD, (
            f"{outcome!r}\n{harness.log_text()}"
        )
        row = harness.session_row(scenario.session_id)
        assert row["status"] == "done", (
            f"status={row['status']} err={row['last_validation_error']}\n"
            + harness.log_text()
        )


def test_s13_codex_argv_wiring(tmp_path) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    daemon_codex_home = tmp_path / "daemon-codex-home"
    # trust_codex_workspace reads CODEX_HOME from the DAEMON env
    # (main.rs:1553) — point it at a scratch dir so the pre-launch trust
    # write cannot touch the operator's real ~/.codex
    with AgentdHarness(extra_env={"CODEX_HOME": str(daemon_codex_home)}) as harness:
        scenario = harness.scenario(
            "s13-codex",
            [
                {"render": "F-idle-codex"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"record_env": ["CODEX_HOME", "CLAUDE_CONFIG_DIR"]},
            ],
        )
        scenario.launch_m1(
            agent_type="codex",
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
            extra_env={"CODEX_HOME": str(codex_home)},
        )

        args = _started_argv(scenario)
        assert "--yolo" in args, args

        command_arg = f'mcp_servers."doeff_result".command='
        command_entries = [a for a in args if a.startswith(command_arg)]
        assert len(command_entries) == 1, args
        # Same self-wiring contract as the claude leg: the daemon under test
        # wires its own binary (seam-compatible, not a hardcoded name).
        assert command_entries[0] == f'{command_arg}"{harness.agentd_bin}"', (
            command_entries
        )

        expected_args_entry = (
            'mcp_servers."doeff_result".args='
            f'["report-result-mcp","--session","{scenario.session_id}",'
            f'"--socket","{harness.socket_path}"]'
        )
        assert expected_args_entry in args, args
        # each config override travels behind its own -c
        assert args.count("-c") >= 2, args

        # session_env really reached the agent process (M1 plumbing)
        deadline = time.monotonic() + 10.0
        env_entries = []
        while time.monotonic() < deadline and not env_entries:
            env_entries = [e for e in scenario.journal() if e["event"] == "env"]
            time.sleep(0.2)
        assert env_entries and env_entries[0]["values"]["CODEX_HOME"] == str(codex_home), (
            env_entries
        )
