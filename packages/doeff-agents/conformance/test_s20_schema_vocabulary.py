"""S20 result-contract validation is JSON Schema — the spec is the authority.

History (doeff#482 / ACP plan U1 ruling, 2026-07-06): the original Rust
agentd implemented validation as an UNAPPROVED subset of JSON Schema
(oneOf/const/type/minLength/pattern/required/properties) that silently
ignored every other keyword.  The C3 port faithfully reproduced it, and
the parity suite promoted that omission into a "contract".  Live failure:
an ACP argus steward reported `decisions` as an array of STRINGS against
a schema declaring `items: {type: object}` — accepted by agentd, caught
only downstream, costing a human gate instead of the in-session
solicitation retry ADR 0035 exists to provide.

User ruling: writing contracts in JSON Schema means the semantics ARE the
JSON Schema specification.  The subset was a deviation, not an alternate
spec.  The validator now imports the settled external semantics
(the `jsonschema` reference implementation; spec conformance is inherited
from its upstream CI against the official JSON-Schema-Test-Suite).  The
retired Rust implementation is not a correctness reference for anything;
no test pins its behavior as expected, not even historically.

S20 freezes the restored contract on the canonical Hy session host:

  (a) `items` violations are rejected at report time -> solicitation ->
      the agent fixes its payload in-session (same loop as S4) — the
      exact incident shape, end to end.
  (b) malformed schemas (meta-schema violations) are rejected at
      `session.launch` (fail-closed): a session is never created with a
      contract that cannot validate.
"""

import json
import os

import pytest
from harness import AgentdHarness
from doeff_agents.agentd_client import AgentdClientError

# Transfer-gate seam (harness.build_agentd): S20 requires the canonical Hy
# host — the retired Rust implementation fail-opens on these keywords
# (doeff#482) and is not a correctness reference.
HY_GATE = bool(os.environ.get("CONFORMANCE_AGENTD_BIN"))
pytestmark = pytest.mark.skipif(
    not HY_GATE,
    reason=(
        "S20 asserts the JSON Schema contract on the canonical Hy host; "
        "set CONFORMANCE_AGENTD_BIN (the retired Rust impl predates the "
        "restored semantics and is not a reference)"
    ),
)

PROMPT = "Produce the conformance structured result."
SOLICITATION_MARKER = "AGENTD RESULT CONTRACT"

# REAL production contract, verbatim from ACP
# apps/argus/shared/attend_report.hy ATTEND-REPORT-SCHEMA (2026-07-06) —
# the exact contract the live incident violated.  Fixture is a pinned
# copy: it freezes the incident-era shape as a contract-class regression,
# it is not a live mirror of the ACP source.
ATTEND_REPORT_SCHEMA = {
    "type": "object",
    "required": ["assessment", "decisions"],
    "properties": {
        "assessment": {"type": "string", "minLength": 1},
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["decision"],
                "properties": {
                    "decision": {"type": "string", "minLength": 1},
                    "why": {"type": "string"},
                },
            },
        },
        "actions": {"type": "array", "items": {"type": "string"}},
        "strategy_update": {"type": "string"},
        "inbox_replies": {"type": "array", "items": {"type": "string"}},
        "next_hint": {"type": "string"},
    },
}
# The live incident payload shape (2026-07-06, ACP sandbox
# inv_wi_57cbac033483bed5_a1): decisions reported as an array of STRINGS.
INVALID_PAYLOAD = {
    "assessment": "s20 incident shape",
    "decisions": ["did a thing", "did another thing"],
}
VALID_PAYLOAD = {
    "assessment": "s20 incident shape",
    "decisions": [{"decision": "did a thing", "why": "because"}],
}

# REAL production contract, verbatim from ACP
# apps/hypha/runtime/registration.hy MERGE-RESULT-SCHEMA (2026-07-06) —
# failureKind's enum was silently unenforced by the retired subset
# (hard rule 7's failure taxonomy hinged on an unvalidated field).
MERGE_RESULT_SCHEMA = {
    "type": "object",
    "required": [
        "validatedHeadSha",
        "validatedBaseSha",
        "summary",
        "validationPassed",
        "merged",
        "failureKind",
    ],
    "properties": {
        "validatedHeadSha": {"type": "string", "minLength": 1},
        "validatedBaseSha": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
        "validationPassed": {"type": "boolean"},
        "merged": {"type": "boolean"},
        "failureKind": {
            "type": "string",
            "enum": ["none", "stale_head", "stale_base", "validation_failed", "agent_error"],
        },
        "validationCommands": {"type": "array", "items": {"type": "string"}},
    },
}
MERGE_INVALID_PAYLOAD = {
    "validatedHeadSha": "a" * 40,
    "validatedBaseSha": "b" * 40,
    "summary": "s20 merge shape",
    "validationPassed": True,
    "merged": False,
    "failureKind": "bogus-taxonomy-value",
}
MERGE_VALID_PAYLOAD = dict(MERGE_INVALID_PAYLOAD, failureKind="validation_failed")


def test_s20_attend_report_items_violation_rejected_then_fixed() -> None:
    with AgentdHarness(extra_serve_args=["--prompt-judge-cmd", ""]) as harness:
        scenario = harness.scenario(
            "s20",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"render": "F-turn-activity-claude"},
                {"report_result": {"payload": INVALID_PAYLOAD}},
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": SOLICITATION_MARKER, "timeout_s": 30}},
                {"report_result": {"payload": VALID_PAYLOAD}},
                {"render": "F-idle-claude"},
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": ATTEND_REPORT_SCHEMA},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=30.0)

        assert outcome.result == VALID_PAYLOAD, (
            f"await_result payload drifted: {outcome.result!r}\n{harness.log_text()}"
        )
        assert outcome.validation_error is None

        report_entries = [
            entry for entry in scenario.journal() if entry["event"] == "report_result"
        ]
        assert len(report_entries) == 2, report_entries
        first_response = json.loads(report_entries[0]["response"])
        assert first_response["result"]["isError"] is True, report_entries[0]
        first_text = first_response["result"]["content"][0]["text"]
        assert "does not satisfy its schema" in first_text, report_entries[0]
        # the rejection names the offending element, so the agent can fix it
        # (the live incident would have self-repaired in-session with this)
        assert "decisions[" in first_text, first_text  # names the offending element (best_match picks one)


def test_s20_merge_result_enum_violation_rejected_then_fixed() -> None:
    with AgentdHarness(extra_serve_args=["--prompt-judge-cmd", ""]) as harness:
        scenario = harness.scenario(
            "s20-merge",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                {"render": "F-turn-activity-claude"},
                {"report_result": {"payload": MERGE_INVALID_PAYLOAD}},
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": SOLICITATION_MARKER, "timeout_s": 30}},
                {"report_result": {"payload": MERGE_VALID_PAYLOAD}},
                {"render": "F-idle-claude"},
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": MERGE_RESULT_SCHEMA},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=30.0)

        assert outcome.result == MERGE_VALID_PAYLOAD, (
            f"await_result payload drifted: {outcome.result!r}\n{harness.log_text()}"
        )
        assert outcome.validation_error is None

        report_entries = [
            entry for entry in scenario.journal() if entry["event"] == "report_result"
        ]
        assert len(report_entries) == 2, report_entries
        first_response = json.loads(report_entries[0]["response"])
        assert first_response["result"]["isError"] is True, report_entries[0]
        first_text = first_response["result"]["content"][0]["text"]
        assert "does not satisfy its schema" in first_text, report_entries[0]
        assert "failureKind" in first_text, first_text


def test_s20_malformed_schema_rejected_at_launch() -> None:
    with AgentdHarness(extra_serve_args=["--prompt-judge-cmd", ""]) as harness:
        scenario = harness.scenario(
            "s20-admission",
            [
                {"render": "F-idle-claude"},
            ],
        )
        with pytest.raises(AgentdClientError) as excinfo:
            scenario.launch_m2(
                prompt=PROMPT,
                expected_result={
                    "payload_schema": {
                        # meta-schema violation: `required` must be an array
                        "type": "object",
                        "required": "not-an-array",
                    }
                },
            )
        assert "not a valid JSON Schema" in str(excinfo.value), excinfo.value
