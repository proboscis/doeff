"""coordinator の core が行の送り先を知らない事の陽性対照と弁別(card ki-0b0ab40048b9・設計検証 lt-D8Y7NDS3A1XHEP1DQJ323D6CHB)。

送りの配線(line_stamp の process_lines)は入口 coordinator/entry/main.hy だけが持つ。code-quality の module doeff-cluster-coordinator
は core と入口を 1 つに持つので、入口のための許しが core にも効く — 規則が core からの import を断る。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

RULE = "doeff-cluster-coordinator-core-does-not-know-where-its-lines-go"
FIXTURE = "packages/doeff-cluster/src/doeff_cluster/coordinator/core/line_stamp_import_forbidden.hy"
ENTRY = "packages/doeff-cluster/src/doeff_cluster/coordinator/entry/main.hy"
CORE = "packages/doeff-cluster/src/doeff_cluster/coordinator/core/program.hy"


def _hits(findings: list[dict], rule: str) -> list[int]:
    return sorted(finding["start"]["line"] for finding in findings if finding["check_id"].endswith(rule))


def test_the_core_importing_the_line_shipping_is_rejected() -> None:
    root = Path(__file__).resolve().parents[2]
    findings = _semgrep_results(root / ".semgrep.yaml", FIXTURE, cwd=root / "tests/semgrep/fixtures/python")
    # 2 行目 = line_stamp の import。4 行目(送りに触れない import)は当たらない。
    assert _hits(findings, RULE) == [2]


def test_the_entry_wires_the_shipping_and_the_core_stays_green() -> None:
    root = Path(__file__).resolve().parents[2]
    # 対照の前提: 入口には現に綴りが在る(無ければ下の緑は範囲の外れを示さない)。
    assert "doeff_cluster.worker.entry.line_stamp" in (root / ENTRY).read_text(encoding="utf-8")
    assert _hits(_semgrep_results(root / ".semgrep.yaml", ENTRY, cwd=root), RULE) == []
    assert _hits(_semgrep_results(root / ".semgrep.yaml", CORE, cwd=root), RULE) == []
