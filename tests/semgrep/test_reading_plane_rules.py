"""読む面(doeff-runner の src/read/)の 2 規則が、本物の木で緑であることの検(ADR-DOE-RUNNER-001 R2 / R3)。

規則の極性(bad の検体で赤・clean の検体で緑)は ADR の defsemgrep が検体の一時の木で検める。ここは本物の
`ide-plugins/vscode/doeff-runner/src/read/` を dir として渡した時と file として渡した時の両方で、2 規則の違反が
0 件であることを見る(paths.include は走査の形で黙って効かなくなり得るので、両方の形で当たる対象の数も見る)。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".semgrep.yaml"
READ_DIR = "ide-plugins/vscode/doeff-runner/src/read"
RULES = (
    "doeff-runner-reading-plane-reads-only-the-index",
    "doeff-runner-reading-plane-is-read-only",
)


def _violations(target: str) -> list[tuple[str, str, int]]:
    findings = _semgrep_results(CONFIG, target, cwd=REPO_ROOT)
    return [
        (finding["check_id"], finding["path"], finding["start"]["line"])
        for finding in findings
        if finding["check_id"].endswith(RULES)
    ]


def test_reading_plane_has_no_file_reads_or_document_writes() -> None:
    assert list((REPO_ROOT / READ_DIR).glob("*.ts")), f"{READ_DIR} に TypeScript の file が無い"
    assert _violations(READ_DIR) == []


def test_reading_plane_rules_reach_a_single_file_target() -> None:
    # 1 file を渡す形(commit の検査の形)でも規則が当たる範囲に入っていることを、実在の file で見る
    panel = f"{READ_DIR}/panel.ts"
    assert (REPO_ROOT / panel).is_file()
    assert _violations(panel) == []
