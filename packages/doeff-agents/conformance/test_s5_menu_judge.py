"""S5 menu disambiguation (contract README S5, tag P, mode M2, codex).

A codex blocking menu renders the same `› ` glyph as the idle REPL prompt,
so it reads as turn-end. ADR-DOE-AGENTS-002 R6: at the turn-end judgment
point the judge runs BEFORE solicitation — pasting the solicitation into a
menu would press Enter on an arbitrary option. The scripted judge returns
{blocked: true, keys: ["Enter"]}; agentd sends Enter; the agent then works
and reports.

Judge wiring: the scripted judge runs via `sh -c`, so the deterministic
verdict table travels as env-var prefixes inside the judge command itself —
no harness change needed.
"""

from __future__ import annotations

import json
import shlex
import sys

from harness import RESULT_SCHEMA, JUDGE_SCRIPT, AgentdHarness

PROMPT = "Resolve the menu then report."
PAYLOAD = {"summary": "unblocked", "ok": True}


def test_s5_judge_unblocks_menu_before_solicitation(tmp_path) -> None:
    table_path = tmp_path / "judge-table.json"
    judge_journal = tmp_path / "judge-journal.jsonl"
    table_path.write_text(
        json.dumps(
            [
                {
                    "contains": "Press enter to confirm",
                    "verdict": {
                        "blocked": True,
                        # Down first: a bare Enter is indistinguishable from
                        # the paste-confirm Enters agentd re-sends after the
                        # prompt (main.rs:2581), which land AFTER the menu
                        # renders and would satisfy a "\r" await without the
                        # judge ever running. The arrow escape sequence can
                        # only come from send_unblock_keys.
                        "keys": ["Down", "Enter"],
                        "reason": "rate-limit menu",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    judge_cmd = (
        f"CONFORMANCE_JUDGE_TABLE={shlex.quote(str(table_path))} "
        f"CONFORMANCE_JUDGE_JOURNAL={shlex.quote(str(judge_journal))} "
        f"{shlex.quote(sys.executable)} {shlex.quote(str(JUDGE_SCRIPT))}"
    )
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    with AgentdHarness(extra_serve_args=["--prompt-judge-cmd", judge_cmd]) as harness:
        scenario = harness.scenario(
            "s5",
            [
                {"render": "F-idle-codex"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                # the awaiting_response latch clears ONLY on an active
                # marker / turn activity (main.rs:3629) — a real codex works
                # first and THEN trips the menu; without this frame turn-end
                # stays suppressed and the judge never runs
                {"render": "F-active-codex"},
                # HOLD the active frame until the monitor has actually seen
                # it: the session row is upserted only after launch's paste +
                # confirm loop finishes (main.rs:1794-1830), so a frame
                # retired earlier is invisible and the latch never clears
                {"await_monitor_ack": {"timeout_s": 30}},
                # codex redraws its TUI when the menu appears — retire the
                # "working (" row (tail-30 active marker) the same way
                {"scroll": 35},
                {"render": "F-menu-codex"},
                # wait for the judge's Down arrow (ESC [ B) — see the
                # verdict table note on why not "\r"
                {"await_keys": {"expect": "[B", "timeout_s": 30}},
                {"render": "F-active-codex"},
                {"report_result": {"payload": PAYLOAD}},
                {"render": "F-idle-codex"},
            ],
        )
        scenario.launch_m2(
            agent_type="codex",
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
            extra_env={"CODEX_HOME": str(codex_home)},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=40.0)
        assert outcome.result == PAYLOAD, (
            f"{outcome!r}\n{harness.log_text()}"
        )

        events = harness.events(scenario.session_id)
        types = [e["event_type"] for e in events]
        assert "session_prompt_unblocked" in types, types
        # R6: the judge resolved the menu; no solicitation was ever pasted
        # into it (Enter on a menu would have confirmed an arbitrary option)
        assert "session_result_solicited" not in types, types

        row = harness.session_row(scenario.session_id)
        assert row["status"] == "done"
        assert row["prompt_unblock_attempts"] == 1
        assert row["result_solicitations_used"] == 0

        # the scripted judge really produced the verdict (not a fast-path)
        judged = [
            json.loads(line)
            for line in judge_journal.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(entry["verdict"]["blocked"] for entry in judged), judged
