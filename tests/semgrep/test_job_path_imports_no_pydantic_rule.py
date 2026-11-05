"""Test of the rule that job-path modules must not import pydantic.

The rules doeff-job-path-imports-no-pydantic (Python) and doeff-job-path-imports-no-pydantic-hy (Hy) reject modules in doeff's root and packages/ that
import pydantic or pydantic_core. Out of scope are
doeff-openai, doeff-gemini, and doeff-openrouter, which receive LLM output, the parity tool doeff_hy/_wire_parity.py, and tests.

- Test 1: in the sample (tests/semgrep/fixtures/python/packages/doeff-job-path-pydantic-sample/) only lines marked `fires` fire (imports only at type-check time,
  imports inside __get_pydantic_core_schema__ (the hook pydantic itself calls), and the similarly named but different package pydantic_settings do not fire).
- Test 2: real files that keep importing pydantic (type-check-time only, inside pydantic's hook, the parity tool, out-of-scope packages) do not fire.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _rule_start_lines, _semgrep_results

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".semgrep.yaml"
RULES = {".py": "doeff-job-path-imports-no-pydantic", ".hy": "doeff-job-path-imports-no-pydantic-hy"}
FIXTURE_ROOT = REPO_ROOT / "tests" / "semgrep" / "fixtures" / "python"
SAMPLE = Path("packages") / "doeff-job-path-pydantic-sample"


def _marked_lines(path: Path) -> set[int]:
    """Line numbers of the lines that should be hit (those with the `fires` marker) -- so the sample side records what the rule blocks."""
    return {
        number
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if line.rstrip().endswith("fires")
    }


@pytest.mark.parametrize("name", ["job_path_sample.py", "job_path_sample.hy"])
def test_only_the_marked_pydantic_imports_fire(name: str) -> None:
    target = SAMPLE / name
    results = _semgrep_results(CONFIG, str(target), cwd=FIXTURE_ROOT)
    expected = _marked_lines(FIXTURE_ROOT / target)
    assert expected
    assert _rule_start_lines(results, RULES[Path(name).suffix]) == expected


#: Real files that import pydantic yet do not fire: imports only at type-check time and imports inside pydantic's hook (json_value.py),
#: the parity tool (wire_parity.py), and the out-of-scope LLM package (doeff-openai). The whole tree is fired on by the commit hook and the landing gate's semgrep baseline
#: comparison (firing on the whole tree exceeds 100 seconds, so it is not done here).
KEPT_PYDANTIC_IMPORTS = (
    "packages/doeff-hy/src/doeff_hy/_json_value.py",
    "packages/doeff-hy/src/doeff_hy/_wire_parity.py",
    "packages/doeff-openai/src/doeff_openai/_structured_llm.py",
)


@pytest.mark.parametrize("target", KEPT_PYDANTIC_IMPORTS)
def test_the_kept_pydantic_imports_of_the_tree_do_not_fire(target: str) -> None:
    assert "pydantic" in (REPO_ROOT / target).read_text(encoding="utf-8")
    results = _semgrep_results(CONFIG, target, cwd=REPO_ROOT)
    assert [result["start"]["line"] for result in results if result["check_id"].endswith(tuple(RULES.values()))] == []
