"""席の家へ運ぶ共通の指示の 2 規則(D10 / D11)の陽性対照と弁別。

card acp:kanban-issue:ki-62aa1f4e9c9c / 依頼 lt-JH7GM60YZ02MSGKKNXTDQK79A1。

守る性質は 2 つ:

* **D10**(`host-checkout-layout-is-never-spelled-here`)は、宿の checkout の layout を
  **path の要素として** `dotfiles` と綴るどの書き方にも当たる。引用符の直後だけを見る形は、
  `(os.path.join home "dotfiles" …)` や `Path(home) / "dotfiles"` や単引用符を見逃し、
  逆に `my-dotfiles/` `acp.dotfiles/` へ誤って当たる(この file の弁別で実測)。
* **D11**(`seat-home-instruction-names-have-one-home`)の 3 本目は**行内に閉じる**。
  `[^"]` は改行を含むので、散文の中の引用符 1 つと数行下の綴りを 1 致に巻き込む。

⚠ 名指しで通す 1 件(`"dotfiles/cron_management"` = 別便の card acp:kanban-issue:ki-6f8425e88733)
が、広げた形でも現に通ることをこの対照が押さえる(pattern-not-regex は 1 致の範囲へ当たるので、
形を広げると黙って効かなくなり得る)。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests/semgrep/fixtures/python"
FIXTURE_DIR = "packages/doeff-agents/src/doeff_agents/sessionhost"

D10 = "host-checkout-layout-is-never-spelled-here"
D11 = "seat-home-instruction-names-have-one-home"

#: 旧形(着地した L247 の綴り)— 弁別のためだけにここで組む。出荷の形は .semgrep.yaml の 1 点。
OLD_SHAPES = """
rules:
  - id: old-d10
    languages: [generic]
    severity: ERROR
    message: old D10 (quote-adjacent only)
    patterns:
      - pattern-regex: '"[^"\\n]*dotfiles/[^"\\n]*"'
      - pattern-not-regex: '"dotfiles/cron_management"'
  - id: old-d11-third
    languages: [generic]
    severity: ERROR
    message: old D11 third shape (crosses newlines)
    pattern-regex: '"[^"]*/(CLAUDE\\.md|skills)"'
"""


def _lines(findings: list[dict], rule: str, filename: str) -> list[int]:
    return sorted(
        finding["start"]["line"]
        for finding in findings
        if finding["check_id"].endswith(rule) and finding["path"].endswith(filename)
    )


@pytest.fixture(scope="module")
def shipped() -> list[dict]:
    """出荷の .semgrep.yaml を 3 つの対照 file へ 1 回だけ撃つ。"""
    return _semgrep_results(REPO_ROOT / ".semgrep.yaml", FIXTURE_DIR, cwd=FIXTURE_ROOT)


@pytest.fixture(scope="module")
def old(tmp_path_factory: pytest.TempPathFactory) -> list[dict]:
    config = tmp_path_factory.mktemp("old-shapes") / "old.yaml"
    config.write_text(OLD_SHAPES, encoding="utf-8")
    return _semgrep_results(config, FIXTURE_DIR, cwd=FIXTURE_ROOT)


def test_d10_catches_every_way_of_spelling_the_host_layout(shipped: list[dict]) -> None:
    # 7〜14 行 = 引用符の直後 / 絶対 path / ~ / f-string / os.path.join / Path の / /
    # 単引用符 / 要素 1 語。15 行以降(my-dotfiles・dotfiles-base・acp.dotfiles・
    # 名指しで通す "dotfiles/cron_management")には当たらない。
    assert _lines(shipped, D10, "host_checkout_layout_forbidden.py") == [7, 8, 9, 10, 11, 12, 13, 14]
    assert _lines(shipped, D10, "host_checkout_layout_forbidden.hy") == [3, 4, 5, 6]


def test_d11_third_shape_closes_inside_one_line(shipped: list[dict]) -> None:
    assert _lines(shipped, D11, "seat_home_instruction_names_forbidden.hy") == [6, 7, 8]


def test_old_shapes_are_the_ones_this_bundle_replaced(old: list[dict]) -> None:
    """弁別: 旧形は見逃し・誤爆・改行またぎの 3 つを現に持っていた。"""
    # 見逃し: os.path.join(11)・Path の /(12)・単引用符(13)・要素 1 語(14)
    py = _lines(old, "old-d10", "host_checkout_layout_forbidden.py")
    assert py == [7, 8, 9, 10, 17, 19]
    # 誤爆: 17 = "my-dotfiles/claude"・19 = "acp.dotfiles/claude"
    assert 17 in py
    assert 19 in py
    # 改行またぎ: 10 行目の散文の引用符から 11 行目の綴りまでを 1 致にする
    straddle = [
        (finding["start"]["line"], finding["end"]["line"])
        for finding in old
        if finding["check_id"].endswith("old-d11-third")
        and finding["path"].endswith("seat_home_instruction_names_forbidden.hy")
    ]
    assert (10, 11) in straddle
