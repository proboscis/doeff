"""Positive control and discrimination: doeff-agents does not decode the shape of external CLI lines (JSON) itself (blind check B of the design verification).

Layer rule: only layer 2 (the lines of doeff-codex) knows the shape of codex lines; doeff-agents (a layer 3 adapter) reads only layer 2's typed records.
Blind check B measured that an implementation where layer 2 carries the tool item without reading its contents and layer 3 decodes codex JSON with its own defwire passes the named test,
pre-commit, and the type checker. The rule doeff-agents-decodes-no-wire-json rejects that shape (defwire, doeff_hy._wire,
OpaqueJson, parse-json) in the src of doeff-agents.

- Test 1: the failing shapes in the fixture (wire type import, OpaqueJson, defwire, parse-json) are each rejected per line. The passing shape (import of a layer 2
  record) is not rejected.
- Test 2: the real codex adapter (handlers/headless_codex.hy) reads only layer 2 records, so it is not rejected.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

RULE = "doeff-agents-decodes-no-wire-json"
ADAPTER_FILE = "packages/doeff-agents/src/doeff_agents/_handlers/headless_codex.hy"
FIXTURE = "packages/doeff-agents/src/doeff_agents/_handlers/codex_wire_json_forbidden.hy"


def _hits(findings: list[dict], rule: str) -> list[int]:
    return sorted(finding["start"]["line"] for finding in findings if finding["check_id"].endswith(rule))


def test_wire_json_decoding_in_doeff_agents_is_rejected() -> None:
    root = Path(__file__).resolve().parents[2]
    findings = _semgrep_results(root / ".semgrep.yaml", FIXTURE, cwd=root / "tests/semgrep/fixtures/python")
    # line 2 = import of doeff_hy._wire / line 3 = OpaqueJson / line 4 = defwire / line 7 = parse-json.
    # Line 9 (import of a layer 2 record) passes.
    assert _hits(findings, RULE) == [2, 3, 4, 7]


def test_the_codex_adapter_reads_only_layer_two_records() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / ADAPTER_FILE).read_text(encoding="utf-8")
    # Control premise: the real adapter reads layer 2 records (CodexLine and the line types at the doeff_codex entry); if it did not, the green below would be meaningless.
    assert "(import doeff_codex [" in source and " CodexLine " in source
    findings = _semgrep_results(root / ".semgrep.yaml", ADAPTER_FILE, cwd=root)
    assert _hits(findings, RULE) == []
