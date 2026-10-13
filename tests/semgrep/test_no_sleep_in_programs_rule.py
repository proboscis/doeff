"""doeff-no-sleep-in-programs の極性の検(card ki-94380dba532f)。

検体(tests/semgrep/fixtures/python/packages/doeff-sleep-sample/src/)で `# 鳴る` を付けた行だけが当たることを、規則の当たりの
行で突き合わせる。鳴るべき形 = @do の program の中の time.sleep・Await(asyncio.sleep(x))・`from time import sleep` の sleep・
@doeff.do の program・program の中の入れ子の helper。鳴らないべき形 = Delay・sleep(0)(asyncio.sleep(0, result=…) を含む)・
(effect, k) を受ける handler の実装(関数と method)・program でない関数。
範囲は本体(src)と examples/(`/packages/*/examples/` と根の `/examples/`)— 後者は test_examples_are_in_the_range が一時の木で見る。
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
FIRES = "# 鳴る"


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


# examples/ の program(本体が手本にする物)。範囲は規則の paths.include の `/packages/*/examples/` と `/examples/` で、
# 本物の根から相対で書かれている — 一時の木を根にして、その 2 つの glob が当たる事を見る。
EXAMPLE_PROGRAM = """\
import asyncio
import time

from doeff import Await, do
from doeff_time import Delay


@do
def waits_in_an_example():
    time.sleep(0.1)  # 鳴る
    yield Await(asyncio.sleep(0.2))  # 鳴る
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
