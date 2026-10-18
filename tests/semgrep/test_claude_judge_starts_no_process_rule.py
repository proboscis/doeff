"""doeff-claude-judge が Claude の CLI の process を自分で起こさないことの陽性対照と弁別。

判定の handler は 1 回きりの print の effect(doeff_claude_code の ClaudePrintOnce)を出すだけで、process(argv・資格の置き場・
待ちの上限・止め方)は doeff-claude-code の handler が持つ。規則 doeff-claude-judge-starts-no-process は、doeff-claude-judge の src で
process を起こす形(subprocess の import・os.system・asyncio の子 process)を断る — CLI の綴りと資格の渡し方の 2 か所目を作らない。

- 1 本目: fixture の失敗の形(1・2 行目の subprocess の import・8 行目の os.system・9 行目の asyncio の子 process)が断られ、
  effect を返すだけの関数は断られない。
- 2 本目: 実物の package の src は断られない。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

RULE = "doeff-claude-judge-starts-no-process"
FIXTURE = "packages/doeff-claude-judge/src/doeff_claude_judge/process_forbidden.py"
PACKAGE_SRC = "packages/doeff-claude-judge/src"


def _hits(findings: list[dict], rule: str) -> list[int]:
    return sorted(finding["start"]["line"] for finding in findings if finding["check_id"].endswith(rule))


def test_starting_a_process_in_the_judge_package_is_rejected() -> None:
    root = Path(__file__).resolve().parents[2]
    findings = _semgrep_results(root / ".semgrep.yaml", FIXTURE, cwd=root / "tests/semgrep/fixtures/python")
    assert _hits(findings, RULE) == [1, 2, 8, 9]


def test_the_judge_package_leaves_the_process_to_the_print_effect() -> None:
    root = Path(__file__).resolve().parents[2]
    findings = _semgrep_results(root / ".semgrep.yaml", PACKAGE_SRC, cwd=root)
    assert _hits(findings, RULE) == []
