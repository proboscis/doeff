"""doeff-agents-no-sleep-effect-hy の極性の検。

doeff-agents は自前の待ちの効果を持たない — program は doeff_time の Delay を yield する(ADR-DOE-TIME-001 R7)。Python の定義と import は
doeff-agents-no-sleep-effect が見るが、Hy の定義(io_effects.hy の Sleep・sessionhost/effects.hy の ClockSleep の形)には当たらなかった。
検体で `; 鳴る` を付けた行だけが当たることを、規則の当たりの行で突き合わせる。鳴るべき形 = 名に Sleep / Delay を含む EffectBase の
defclass・名が sleep か -sleep で終わる構築子の定義。鳴らないべき形 = 待ちでない効果・EffectBase を継がない型・Delay で待つ program・
fake の handler の DelayEffect の節。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".semgrep.yaml"
RULE = "doeff-agents-no-sleep-effect-hy"
FIXTURE_ROOT = REPO_ROOT / "tests" / "semgrep" / "fixtures" / "python"
SAMPLE = Path("packages/doeff-agents/src/doeff_agents/own_wait_effect_forbidden.hy")
FIRES = "; 鳴る"


def test_only_the_marked_lines_fire() -> None:
    findings = _semgrep_results(CONFIG, str(SAMPLE), cwd=FIXTURE_ROOT)
    hits = sorted(finding["start"]["line"] for finding in findings if finding["check_id"].endswith(RULE))
    expected = sorted(
        number
        for number, line in enumerate((FIXTURE_ROOT / SAMPLE).read_text(encoding="utf-8").splitlines(), start=1)
        if line.rstrip().endswith(FIRES)
    )
    assert len(expected) == 5, "the sample's marked lines changed"
    assert hits == expected
