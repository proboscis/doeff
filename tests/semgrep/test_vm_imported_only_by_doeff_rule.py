"""Test of doeff-vm-imported-only-by-doeff.

Only doeff imports doeff_vm. Hy in subpackages of packages/, examples, and
docs/adr read VM types from the doeff root and the VM's internals from doeff._vm. Check against the rule's hit lines that, in the sample
(tests/semgrep/fixtures/python/packages/doeff-vm-boundary-sample/), only the lines marked `fires` are hit, by comparing against the rule's hit lines.

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
    """The numbers of lines in the sample that should be hit (lines with the `fires` mark) -- so that what the rule stops is written on the sample side."""
    return {
        number
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if line.rstrip().endswith("fires")
    }


@pytest.mark.parametrize("name", ["vm_import_sample.py", "vm_import_sample.hy"])
def test_only_the_marked_imports_fire(name: str) -> None:
    """Only imports of doeff_vm (plain import, submodule, Hy import) fire; imports of the doeff root and doeff._vm do not."""
    target = SAMPLE / name
    results = _semgrep_results(CONFIG, str(target), cwd=FIXTURE_ROOT)
    expected = _marked_lines(FIXTURE_ROOT / target)
    assert expected
    assert _rule_start_lines(results, RULE) == expected
