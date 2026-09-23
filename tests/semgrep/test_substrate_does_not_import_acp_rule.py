"""器の kind module(sessionhost/impls/)が acp を import しないことの陽性対照と弁別。

card acp:kanban-issue:ki-554e364641e8 / 設計検証 lt-A5JQ5TRYBQ6GM8E4PTNQHPH0C4 §10.2。

規則が守るのは「記憶の冊についての判断(渡す / 守る / 退役・撃ち先・digest の式)は acp/judgment.hy の
1 点に在り、器はそれを持たない」。盲検 B が示したとおり、既存の substrate-clean の規則は生 IO の
pattern で、import 文は見ていなかった — 器が acp の関数を import して自分で比べる形は、それまで
どの規則にも当たらなかった。

弁別が要るのは、器の file に註として `sessionhost/acp/effects.py` の path が現に書かれているから
(綴りの家を名指す註)— 規則は module の綴り(ドット区切り)にだけ当たり、path の註は通す。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

RULE = "doeff-agents-substrate-does-not-import-acp"
FIXTURE_DIR = "packages/doeff-agents/src/doeff_agents/sessionhost/impls"
BAD = f"{FIXTURE_DIR}/substrate_imports_acp_forbidden.hy"
CLEAN = f"{FIXTURE_DIR}/substrate_imports_only_substrate_effects_clean.hy"
SHIPPED_VESSEL = "packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy"


def _hits(findings: list[dict], rule: str, filename: str) -> list[int]:
    return sorted(
        finding["start"]["line"]
        for finding in findings
        if finding["check_id"].endswith(rule) and finding["path"].endswith(filename)
    )


@pytest.fixture(scope="module")
def shipped() -> list[dict]:
    root = Path(__file__).resolve().parents[2]
    return _semgrep_results(
        root / ".semgrep.yaml", FIXTURE_DIR, cwd=root / "tests/semgrep/fixtures/python"
    )


def test_a_vessel_that_imports_acp_is_rejected(shipped: list[dict]) -> None:
    # 6 行目 = judgment の関数の import / 9 行目 = effects の型の import /
    # 12 行目 = import 文の形を取らない綴りの参照。
    assert _hits(shipped, RULE, "substrate_imports_acp_forbidden.hy") == [6, 9, 12]


def test_a_vessel_that_imports_only_substrate_effects_stays_green(shipped: list[dict]) -> None:
    assert _hits(shipped, RULE, "substrate_imports_only_substrate_effects_clean.hy") == []


def test_the_shipped_claude_vessel_stays_green() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / SHIPPED_VESSEL).read_text(encoding="utf-8")
    # 対照の前提: 出荷の器は註で acp の path を名指している(弁別が意味を持つ)。
    assert "sessionhost/acp/effects.py" in source
    findings = _semgrep_results(root / ".semgrep.yaml", SHIPPED_VESSEL, cwd=root)
    assert _hits(findings, RULE, "claude_code.hy") == []
