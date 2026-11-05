"""Polarity test for doeff-public-api-no-subpackage-imports.

The rule's message says "imports from a subpackage are also allowed -- names at top level from top level, and from the subpackage when a module-specific internal name is needed". The shape aligned with the wording
-- `from doeff.<subpackage> import ...` where every imported name is an internal name starting with `_` -- does not fire, while any form containing
even one public name, or `import doeff.<subpackage>`, does fire.

Only lines with the "fires" marker comment in the sample (tests/semgrep/fixtures/python/tests/public_api/) must be hit; this is checked against the rule's hit lines.
The sample also contains a shape with many `#` in comments inside parentheses -- if the rule's regex repetition regressed, semgrep would emit a SemgrepError and all of the rule's hits
would vanish (measured on 2026-10-12 with the old non-possessive regex), so the firing lines remaining acts as that guard.
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
FIRES = "# fires"
# The test that had the minimal failing case of #2857 (the two public names are taken from top level; only the internal name _MISSING is taken from the subpackage)
PUBLIC_API_TEST = REPO_ROOT / "tests" / "public_api" / "test_mcp_tool_def.py"


def _hits(target: Path) -> list[int]:
    findings = _semgrep_results(CONFIG, str(target.relative_to(REPO_ROOT)), cwd=REPO_ROOT)
    return sorted(
        finding["start"]["line"] for finding in findings if finding["check_id"].endswith(RULE)
    )


def _line_of(text: str) -> int:
    """Line numbers where the whole line equals text in the sample (for a multi-line import, the `from` line -- the rule's hit line)."""
    numbers = [
        number
        for number, line in enumerate(FIXTURE.read_text().splitlines(), start=1)
        if line.rstrip() == text
    ]
    assert len(numbers) == 1, f"the sample does not have exactly one line {text!r}: {numbers}"
    return numbers[0]


def test_only_the_marked_lines_fire() -> None:
    expected = [
        number
        for number, line in enumerate(FIXTURE.read_text().splitlines(), start=1)
        if line.rstrip().endswith(FIRES)
    ]
    hits = _hits(FIXTURE)
    # There are firing lines (also checks that the rule reaches the file and is not dying on every scan)
    assert expected
    assert hits == expected


def test_the_two_examples_of_the_issue() -> None:
    hits = _hits(FIXTURE)
    # An import of only internal names does not fire; an import of a public name does
    assert _line_of("from doeff.mcp import _MISSING") not in hits
    assert _line_of("from doeff.mcp import McpToolDef  # fires") in hits


def test_public_api_test_of_mcp_tool_def_is_not_hit() -> None:
    # Takes the two public names from top level and only _MISSING from the subpackage -- not hit by the rule
    assert _hits(PUBLIC_API_TEST) == []
