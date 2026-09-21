"""席の家へ symlink を据える物理が 1 点から漏れないことの陽性対照と弁別。

card acp:kanban-issue:ki-62aa1f4e9c9c 決定 D8 / 計画段の指摘 lt-AM6TW7CAG9Y994J8RYAZAQQJQ4。

規則が守るのは「据え付けは 1 つの動詞(substrate.hy ensure-symlink-outcome)」で、その動詞だけが
一意な名の仮の symlink を rename(2) 1 手で被せる。外で hand-roll すると、家を同じ拍で共有する
2 席の片方が落ち(実測 200/200・198/200)、張り替えの間に読んだ席が根の無い瞬間を見る(52〜53 %)。

⚠ 弁別が要るのは、この規則が **file 単位の exclude** で出荷の 1 点を通しているから — exclude の
綴りが動くと、規則は黙って本体ごと沈黙する(陽性対照だけでは気づけない)。下の 2 本目が、
出荷の動詞の file には現に綴りが在り、exclude がそれを通していることを押さえる。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

RULE = "symlink-install-has-one-home"
VERB_FILE = "packages/doeff-agents/src/doeff_agents/sessionhost/substrate.hy"
FIXTURE = (
    "packages/doeff-agents/src/doeff_agents/sessionhost/impls/"
    "symlink_install_outside_substrate_forbidden.hy"
)


def _hits(findings: list[dict], rule: str) -> list[int]:
    return sorted(
        finding["start"]["line"] for finding in findings if finding["check_id"].endswith(rule)
    )


def test_symlink_install_outside_the_one_verb_is_rejected() -> None:
    root = Path(__file__).resolve().parents[2]
    findings = _semgrep_results(
        root / ".semgrep.yaml", FIXTURE, cwd=root / "tests/semgrep/fixtures/python"
    )
    # 2 行目 = os.symlink / 3 行目 = Path.symlink_to。委ねる形と読みの syscall は外れる。
    assert _hits(findings, RULE) == [2, 3]


def test_the_one_verb_itself_stays_green() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / VERB_FILE).read_text(encoding="utf-8")
    # 対照の前提: 出荷の動詞の file には現に綴りが在る(無ければ下の緑は無意味)。
    assert "os.symlink" in source
    findings = _semgrep_results(root / ".semgrep.yaml", VERB_FILE, cwd=root)
    assert _hits(findings, RULE) == []
