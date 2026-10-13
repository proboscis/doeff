"""doeff-no-sleep-in-tests の極性の検(agora-redesign #2882)。

検体(tests/semgrep/fixtures/python/tests/)で `# 鳴る` を付けた行だけが当たることを、規則の当たりの行で突き合わせる。
鳴るべき形 = 宣言の無い検・宣言の無い検の入れ子の helper・別の marker だけの検・module の段の helper の sleep・
誰も立てない合図を待つ言い換え threading.Event().wait(秒)。
鳴らないべき形 = 関数の @pytest.mark.realtime(async・入れ子の helper・ほかの marker と重ねた形・class の method を含む)・
module の pytestmark = pytest.mark.realtime より後・sleep(0)・誰かが立てる合図の wait。どちらの検体にも鳴る行を置き、
規則が file に届いていることも見る。

範囲の検(card ki-6aa7cb5ffb83)= 名が tests の dir の外でも検の名の file(test_*.py・*_test.py・conftest.py)には届き、
検の名でない file と、規則の paths.exclude の dir(名簿の [suites])には届かない。
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
FIRES = "# 鳴る"
SAMPLES = ("sleep_function_marks.py", "sleep_module_mark.py")


def _expected_lines(fixture: Path) -> list[int]:
    return [
        number
        for number, line in enumerate(fixture.read_text().splitlines(), start=1)
        if line.rstrip().endswith(FIRES)
    ]


def test_only_the_marked_lines_fire() -> None:
    # semgrep は起動の費用が大きいので、検体の dir に 1 回だけ当てて file ごとに突き合わせる
    findings = _semgrep_results(CONFIG, str(FIXTURES.relative_to(REPO_ROOT)), cwd=REPO_ROOT)
    hits = sorted(
        (Path(finding["path"]).name, finding["start"]["line"])
        for finding in findings
        if finding["check_id"].endswith(RULE)
    )
    expected = sorted((name, line) for name in SAMPLES for line in _expected_lines(FIXTURES / name))
    # どちらの検体にも鳴る行が在る(規則が file に届いていることも見る)
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
