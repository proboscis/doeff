"""S2 solicitation then report (contract README S2, tag P, mode M2).

Turn-end is reached WITHOUT reporting a result: the agent idles, receives
the prompt, shows turn activity, then goes back to idle with no valid
report on file. The monitor's bounded solicitation loop (ADR-DOE-AGENTS-002
R1/R4) pastes the `AGENTD RESULT CONTRACT: ...` corrective message into the
pane; the agent then reports a valid payload and the session finalizes
`done`.

Discovery note: this scenario reaches the turn-end-without-result branch,
which (unless disabled) consults `config.prompt_judge_cmd` for R6 menu
disambiguation -- a REAL `claude -p` invocation by default
(`DEFAULT_PROMPT_JUDGE_CMD`, main.rs:150), which fires on every turn-end
evaluation and violates the suite's own non-goal ("no real model
execution", README「Non-goals」). `harness.py`'s `AgentdHarness.start()`
now disables the judge by default (`--prompt-judge-cmd ""`) unless a
scenario supplies its own via `extra_serve_args` -- this test still passes
`extra_serve_args=["--prompt-judge-cmd", ""]` explicitly (harmless/
redundant now, kept for self-documentation and as a guard against that
default changing back).
"""

from __future__ import annotations

import json

from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."
PAYLOAD = {"summary": "solicited then reported", "ok": True}
SOLICITATION_MARKER = "AGENTD RESULT CONTRACT"


def test_s2_solicitation_then_report() -> None:
    with AgentdHarness(extra_serve_args=["--prompt-judge-cmd", ""]) as harness:
        scenario = harness.scenario(
            "s2",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"render": "F-turn-activity-claude"},
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": SOLICITATION_MARKER, "timeout_s": 30}},
                {"report_result": {"payload": PAYLOAD}},
                {"render": "F-idle-claude"},
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=30.0)

        # wire: the session finalizes done once the corrective report lands
        assert outcome.result == PAYLOAD, (
            f"await_result payload drifted: {outcome.result!r}\n{harness.log_text()}"
        )
        assert outcome.validation_error is None

        # journal: the solicitation text arrives BEFORE the report_result call
        journal = scenario.journal()
        solicitation_index = next(
            i
            for i, entry in enumerate(journal)
            if entry["event"] == "keys"
            and entry.get("expect") == SOLICITATION_MARKER
            and entry["matched"]
        )
        report_index = next(
            i for i, entry in enumerate(journal) if entry["event"] == "report_result"
        )
        assert solicitation_index < report_index, (
            "solicitation must arrive before the corrective report_result call\n"
            f"{journal}"
        )
        report_entry = journal[report_index]
        assert "error" not in json.loads(report_entry["response"]).get("result", {}), (
            f"report_result not accepted: {report_entry}"
        )

        # row: the session was never terminal while the solicitation was
        # pending, done + payload persisted, exactly one solicitation used
        row = harness.session_row(scenario.session_id)
        assert row["status"] == "done", (
            f"status={row['status']} err={row['last_validation_error']}\n"
            + harness.log_text()
        )
        assert json.loads(row["result_payload_json"]) == PAYLOAD
        assert row["result_solicitations_used"] == 1

        events = harness.events(scenario.session_id)
        solicited_events = [e for e in events if e["event_type"] == "session_result_solicited"]
        assert len(solicited_events) == 1, f"expected exactly one solicitation event: {events}"
