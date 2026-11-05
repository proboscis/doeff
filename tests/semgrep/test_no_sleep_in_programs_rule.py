"""Polarity test for doeff-no-sleep-in-programs.

Checks that only the lines marked `# fires` in the fixtures (tests/semgrep/fixtures/python/packages/doeff-sleep-sample/src/) are hit, by matching them against the rule's hit
lines. Shapes that must fire = time.sleep inside a @do program, Await(asyncio.sleep(x)), sleep from `from time import sleep`,
a @doeff.do program, and a nested helper inside a program. Shapes that must not fire = Delay, sleep(0) (including asyncio.sleep(0, result=…)),
handler implementations taking (effect, k) (functions and methods), and functions that are not programs.
The scope is the body (src) and examples/ (`/packages/*/examples/` and the root `/examples/`); the latter is checked on a temporary tree by test_examples_are_in_the_range.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".semgrep.yaml"
RULE = "doeff-no-sleep-in-programs"
FIXTURES = REPO_ROOT / "tests" / "semgrep" / "fixtures" / "python" / "packages" / "doeff-sleep-sample" / "src"
SAMPLE = FIXTURES / "doeff_sleep_sample" / "programs.py"
FIRES = "# fires"


def test_only_the_marked_lines_fire() -> None:
    findings = _semgrep_results(CONFIG, str(FIXTURES.relative_to(REPO_ROOT)), cwd=REPO_ROOT)
    hits = sorted(
        (Path(finding["path"]).name, finding["start"]["line"])
        for finding in findings
        if finding["check_id"].endswith(RULE)
    )
    expected = sorted(
        (SAMPLE.name, number)
        for number, line in enumerate(SAMPLE.read_text().splitlines(), start=1)
        if line.rstrip().endswith(FIRES)
    )
    assert expected, "the sample has no line marked to fire"
    assert hits == expected


# Programs in examples/ (what the body takes as models). The scope is the rule's paths.include `/packages/*/examples/` and `/examples/`,
# written relative to the real root — use a temporary tree as the root and check that those two globs match.
EXAMPLE_PROGRAM = """\
import asyncio
import time

from doeff import Await, do
from doeff_time import Delay


@do
def waits_in_an_example():
    time.sleep(0.1)  # fires
    yield Await(asyncio.sleep(0.2))  # fires
    yield Delay(0.3)
    yield Await(asyncio.sleep(0))
    return "done"
"""
EXAMPLE_DIRS = ("packages/doeff-sample/examples", "examples")


def test_examples_are_in_the_range(tmp_path: Path) -> None:
    for example_dir in EXAMPLE_DIRS:
        example = tmp_path / example_dir / "flow.py"
        example.parent.mkdir(parents=True)
        example.write_text(EXAMPLE_PROGRAM)

    findings = _semgrep_results(CONFIG, ".", cwd=tmp_path, project_root=tmp_path)
    hits = sorted(
        (Path(finding["path"]).as_posix(), finding["start"]["line"])
        for finding in findings
        if finding["check_id"].endswith(RULE)
    )
    fire_lines = [
        number
        for number, line in enumerate(EXAMPLE_PROGRAM.splitlines(), start=1)
        if line.rstrip().endswith(FIRES)
    ]
    assert fire_lines, "the example has no line marked to fire"
    assert hits == sorted(
        (f"{example_dir}/flow.py", number) for example_dir in EXAMPLE_DIRS for number in fire_lines
    )
