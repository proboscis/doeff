"""Polarity test for doeff-no-sleep-in-tests.

Checks that only the lines marked `# fires` in the fixtures (tests/semgrep/fixtures/python/tests/) are hit, by matching them against the lines the rule hits.
Shapes that must fire = a test with no declaration, a nested helper of a test with no declaration, a test with only another marker, a sleep in a module-level helper,
and the rephrasing that waits for a signal nobody sets, threading.Event().wait(seconds).
Shapes that must not fire = a function's @pytest.mark.realtime (including async, nested helpers, stacking with other markers, and class methods),
anything after a module-level pytestmark = pytest.mark.realtime, sleep(0), and a wait on a signal someone sets. Both fixtures contain firing lines,
so we also check that the rule reaches the file.

Scope test: the rule reaches files with test names (test_*.py, *_test.py, conftest.py) even outside a tests dir,
but not files without test names, nor dirs in the rule's paths.exclude (the roster's [suites]).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".semgrep.yaml"
RULE = "doeff-no-sleep-in-tests"
FIXTURES = REPO_ROOT / "tests" / "semgrep" / "fixtures" / "python" / "tests"
FIRES = "# fires"
SAMPLES = ("sleep_function_marks.py", "sleep_module_mark.py")


def _expected_lines(fixture: Path) -> list[int]:
    return [
        number
        for number, line in enumerate(fixture.read_text().splitlines(), start=1)
        if line.rstrip().endswith(FIRES)
    ]


def test_only_the_marked_lines_fire() -> None:
    # semgrep is expensive to start, so run it once on the fixture dir and match per file
    findings = _semgrep_results(CONFIG, str(FIXTURES.relative_to(REPO_ROOT)), cwd=REPO_ROOT)
    hits = sorted(
        (Path(finding["path"]).name, finding["start"]["line"])
        for finding in findings
        if finding["check_id"].endswith(RULE)
    )
    expected = sorted((name, line) for name in SAMPLES for line in _expected_lines(FIXTURES / name))
    # Both fixtures have firing lines (also checks that the rule reaches the file)
    assert {name for name, _ in expected} == set(SAMPLES)
    assert hits == expected


def test_the_rule_reaches_test_named_files_outside_a_tests_dir(tmp_path: Path) -> None:
    sleeping = "import time\n\n\ndef wait():\n    time.sleep(0.1)\n"
    fires = ("suite/conftest.py", "suite/scenario_test.py", "suite/test_scenario.py")
    silent = ("suite/harness.py", "packages/doeff-agents/conformance/test_s1.py")
    for relative in (*fires, *silent):
        sample = tmp_path / relative
        sample.parent.mkdir(parents=True, exist_ok=True)
        sample.write_text(sleeping)
    findings = _semgrep_results(CONFIG, ".", cwd=tmp_path, project_root=tmp_path)
    hits = sorted(
        Path(finding["path"]).as_posix()
        for finding in findings
        if finding["check_id"].endswith(RULE)
    )
    assert hits == sorted(fires)
