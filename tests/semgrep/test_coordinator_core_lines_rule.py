"""Positive control and discrimination that the coordinator's core does not know where lines are sent (design verification).

The sending wiring (line_stamp's process_lines) is held only by the entry coordinator/entry/main.hy. The code-quality module doeff-cluster-coordinator
holds the core and the entry as one, so the allowance made for the entry also applies to the core -- the rule refuses imports from the core.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

RULE = "doeff-cluster-coordinator-core-does-not-know-where-its-lines-go"
FIXTURE = "packages/doeff-cluster/src/doeff_cluster/_coordinator/core/line_stamp_import_forbidden.hy"
ENTRY = "packages/doeff-cluster/src/doeff_cluster/_coordinator/entry/main.hy"
CORE = "packages/doeff-cluster/src/doeff_cluster/_coordinator/core/program.hy"


def _hits(findings: list[dict], rule: str) -> list[int]:
    return sorted(finding["start"]["line"] for finding in findings if finding["check_id"].endswith(rule))


def test_the_core_importing_the_line_shipping_is_rejected() -> None:
    root = Path(__file__).resolve().parents[2]
    findings = _semgrep_results(root / ".semgrep.yaml", FIXTURE, cwd=root / "tests/semgrep/fixtures/python")
    # Line 2 = the import of line_stamp. Line 4 (an import that does not touch sending) is not matched.
    assert _hits(findings, RULE) == [2]


def test_the_entry_wires_the_shipping_and_the_core_stays_green() -> None:
    root = Path(__file__).resolve().parents[2]
    # Premise of the control: the entry does in fact have the spelling (if it did not, the green below would not show that the scope was missed).
    assert "doeff_cluster._worker.entry.line_stamp" in (root / ENTRY).read_text(encoding="utf-8")
    assert _hits(_semgrep_results(root / ".semgrep.yaml", ENTRY, cwd=root), RULE) == []
    assert _hits(_semgrep_results(root / ".semgrep.yaml", CORE, cwd=root), RULE) == []
