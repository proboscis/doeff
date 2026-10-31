"""doeff-agents が外の CLI の行(JSON)の形を自前で解かないことの陽性対照と弁別(設計検証の盲検 B)。

層の決まり: codex の行の形を知るのは層 2(doeff-codex の lines)だけで、doeff-agents(層 3 の adapter)は層 2 の型つきの記録だけを
読む。盲検 B は、層 2 が道具の item を中を読まずに運び、層 3 が自前の defwire で codex の JSON を解く実装が、名指しの検・
pre-commit・型検査を全部通ることを実測した。規則 doeff-agents-decodes-no-wire-json は、その形(defwire・doeff_hy._wire・
OpaqueJson・parse-json)を doeff-agents の src で断る。

- 1 本目: fixture の失敗の形(wire の型の import・OpaqueJson・defwire・parse-json)が、行ごとに断られる。通る形(層 2 の記録の
  import)は断られない。
- 2 本目: 実物の codex の adapter(handlers/headless_codex.hy)は層 2 の記録だけを読むので、断られない。
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
    # 2 行目 = doeff_hy._wire の import / 3 行目 = OpaqueJson / 4 行目 = defwire / 7 行目 = parse-json。
    # 9 行目(層 2 の記録の import)は通る。
    assert _hits(findings, RULE) == [2, 3, 4, 7]


def test_the_codex_adapter_reads_only_layer_two_records() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / ADAPTER_FILE).read_text(encoding="utf-8")
    # 対照の前提: 実物の adapter は層 2 の記録(doeff_codex の入口の CodexLine と行の型)を読んでいる(読んでいなければ下の緑は無意味)。
    assert "(import doeff_codex [" in source and " CodexLine " in source
    findings = _semgrep_results(root / ".semgrep.yaml", ADAPTER_FILE, cwd=root)
    assert _hits(findings, RULE) == []
