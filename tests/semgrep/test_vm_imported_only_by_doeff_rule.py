"""doeff-vm-imported-only-by-doeff の検。

doeff だけが doeff_vm を import する。packages/ の subpackage・examples・
docs/adr の Hy は VM の型を doeff の根から、VM の内側の物を doeff._vm から読む。検体
(tests/semgrep/fixtures/python/packages/doeff-vm-boundary-sample/)で `fires` を付けた行だけが当たることを、規則の当たりの行で
突き合わせる。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _rule_start_lines, _semgrep_results

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".semgrep.yaml"
RULE = "doeff-vm-imported-only-by-doeff"
FIXTURE_ROOT = REPO_ROOT / "tests" / "semgrep" / "fixtures" / "python"
SAMPLE = Path("packages") / "doeff-vm-boundary-sample"


def _marked_lines(path: Path) -> set[int]:
    """検体で、当たるべき行(`fires` の印の在る行)の番号 — 規則が何を止めるかを検体の側に書いておくため。"""
    return {
        number
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if line.rstrip().endswith("fires")
    }


@pytest.mark.parametrize("name", ["vm_import_sample.py", "vm_import_sample.hy"])
def test_only_the_marked_imports_fire(name: str) -> None:
    """doeff_vm の import(素の import・下の module・Hy の import)だけが鳴り、doeff の根と doeff._vm の import は鳴らない。"""
    target = SAMPLE / name
    results = _semgrep_results(CONFIG, str(target), cwd=FIXTURE_ROOT)
    expected = _marked_lines(FIXTURE_ROOT / target)
    assert expected
    assert _rule_start_lines(results, RULE) == expected
