"""doeff-public-api-no-subpackage-imports の極性の検(agora-redesign #2857)。

規則の message は「下の package からの import も許す — top level に在る名は top level から、module 固有の内部の名が要る時は下の
package から」と言う。型を言葉に合わせた形 = 取り込む名が全部 `_` で始まる内部の名の `from doeff.<下の package> import …` は鳴らさず、
公開の名を 1 つでも含む形・`import doeff.<下の package>` は鳴らす。

検体(tests/semgrep/fixtures/python/tests/public_api/)で `# 鳴る` を付けた行だけが当たることを、規則の当たりの行で突き合わせる。
括弧の中の comment に `#` が多い形も検体に置く — 規則の regex の繰り返しが戻る形だと、semgrep が SemgrepError を出して規則の当たりが
全部消える(2026-10-12 に旧い非 possessive の regex で実測)ので、鳴る行が残ることがその guard になる。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".semgrep.yaml"
RULE = "doeff-public-api-no-subpackage-imports"
FIXTURE = REPO_ROOT / "tests" / "semgrep" / "fixtures" / "python" / "tests" / "public_api" / "internal_name_imports.py"
FIRES = "# 鳴る"
# #2857 の最小の失敗ケースが在った検(公開の 2 名は top level から取り、内部の名 _MISSING だけを下の package から取る)
PUBLIC_API_TEST = REPO_ROOT / "tests" / "public_api" / "test_mcp_tool_def.py"


def _hits(target: Path) -> list[int]:
    findings = _semgrep_results(CONFIG, str(target.relative_to(REPO_ROOT)), cwd=REPO_ROOT)
    return sorted(
        finding["start"]["line"] for finding in findings if finding["check_id"].endswith(RULE)
    )


def _line_of(text: str) -> int:
    """検体の中で、行の全部が text に等しい行の番号(複数行の import は `from` の行 — 規則の当たりの行)。"""
    numbers = [
        number
        for number, line in enumerate(FIXTURE.read_text().splitlines(), start=1)
        if line.rstrip() == text
    ]
    assert len(numbers) == 1, f"検体に {text!r} の行が 1 つでない: {numbers}"
    return numbers[0]


def test_only_the_marked_lines_fire() -> None:
    expected = [
        number
        for number, line in enumerate(FIXTURE.read_text().splitlines(), start=1)
        if line.rstrip().endswith(FIRES)
    ]
    hits = _hits(FIXTURE)
    # 鳴る行が在る(規則が file に届いていること・規則が走査ごと死んでいないことも見る)
    assert expected
    assert hits == expected


def test_the_two_examples_of_the_issue() -> None:
    hits = _hits(FIXTURE)
    # 内部の名だけの import は鳴らず、公開の名の import は鳴る
    assert _line_of("from doeff.mcp import _MISSING") not in hits
    assert _line_of("from doeff.mcp import McpToolDef  # 鳴る") in hits


def test_public_api_test_of_mcp_tool_def_is_not_hit() -> None:
    # 公開の 2 名は top level から取り、_MISSING だけを下の package から取る形 — 規則に当たらない
    assert _hits(PUBLIC_API_TEST) == []
