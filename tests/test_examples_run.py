"""examples/ の例の file が今の doeff で script として最後まで走り、書いた通りの結果を印字する事の検(取り組み doeff-publish・card ki-524874f8945a)。

examples/ の例は利用者が写して走らせる読み物で、どの検も走らせていなかったので、API が変わっても誰も気づかなかった:
`Program.of`・`ask`・`default_handlers`・`async_run` が消えた後も、5 file 全部が import か実行の初めで落ちていた
(docs の報告 2026-10-17-doeff-docs-examples-run.html の「古い使い方の訳」)。

examples/ の `.py` は全部この検に入る(新しい例も書いただけで入り、EXPECTED_LINES に行が無ければ赤になる)。
終了コードだけでは足りない: traceback_demo は失敗を起こして捕まえる例なので、起こしたはずの失敗が起きなくても 0 で終わる。
そこで例ごとに、標準出力に必ず出る行を EXPECTED_LINES に置き、全部が出る事を確かめる。例の印字を変えたら、ここも直す。
例は開発の環境で走らせる(llm_multi_provider_stacking は核の外の部品 doeff-gemini・doeff-llm・doeff-openai・
doeff-openrouter を使う)。核の 5 個だけの新しい環境で走る事は、card の発言に結果を書いて確かめた。
1 本が長い例は SLOW に訳つきで置き、印 slow を付ける(`make test` の `-m "not e2e and not slow"` が外す — 名指せば走る)。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.cli.cli_child import with_settings

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
# 子 1 つの制限時間の、負荷で伸ばす前の値(秒)。1 本は 約 1〜3 秒(多くは doeff の import)。
CHILD_DEADLINE_SECONDS = 50

# 例ごとの、標準出力に出るはずの行(行の全体で比べる)。結果の値を印字する行を選ぶ。
EXPECTED_LINES: dict[str, tuple[str, ...]] = {
    "llm_multi_provider_stacking.py": (
        "analysis: verdict='clean' score=8",
        "summary: Gemini summary",
    ),
    "marker_demo.py": (
        "Simple interpreter result: 42",
        "Async interpreter result: 42",
        "Factory interpreter result: run by factory_interpreter",
        "Pipeline result: {'processed': {'a': 2, 'b': 5.0}, 'path': '/tmp/results.json', "
        "'log': ['Processed 2 items', 'Transformation complete', "
        "'would write 2 results to /tmp/results.json']}",
        "Chained programs: 42",
        "Executor result: run by ProgramExecutor",
    ),
    "sample_entrypoints.py": (
        "  hello_world_program -> 'Hello, World!'",
        "  fetch_user_42_program -> {'id': 42, 'name': 'User 42'}",
        "  full_pipeline_program -> {'count': 2, 'ids': [1, 2]}",
        "  formatted_result -> 'Result: 20'",
        "  strategy_pipeline -> [{'id': 1, 'processed': True}, {'id': 2, 'processed': True}, "
        "{'id': 3, 'processed': True}]",
        "  with_logging(hello_world_program) -> 'Hello, World!'",
    ),
    "traceback_demo.py": (
        "caught RuntimeError — run() printed its doeff traceback above",
        "caught KeyError — run() printed its doeff traceback above",
        "caught ValueError — run() printed its doeff traceback above",
    ),
}


# 1 本が長い例と、その訳。
SLOW = {
    "llm_multi_provider_stacking.py": "openai の SDK の import だけで 約 25 秒かかる(2026-10-17 の開発の環境・Python 3.14t)",
}


def _examples() -> list[str]:
    return sorted(path.name for path in EXAMPLES.glob("*.py"))


def _params() -> list[object]:
    return [
        pytest.param(name, marks=pytest.mark.slow) if name in SLOW else name for name in _examples()
    ]


@pytest.mark.parametrize("name", _params())
def test_example_runs_and_prints_its_results(name: str, deadline_scale_factor: float) -> None:
    assert name in EXPECTED_LINES, (
        f"examples/{name} の標準出力に出るはずの行を、tests/test_examples_run.py の EXPECTED_LINES に置く"
    )
    completed = subprocess.run(
        with_settings([sys.executable, str(EXAMPLES / name)], {"DOEFF_DISABLE_PROFILE": "1"}),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )
    assert completed.returncode == 0, (
        f"examples/{name} が終了コード {completed.returncode} で落ちた:\n{completed.stderr[-3000:]}"
    )
    printed = completed.stdout.splitlines()
    missing = [line for line in EXPECTED_LINES[name] if line not in printed]
    assert missing == [], (
        f"examples/{name} が印字しなかった行: {missing}\n印字:\n{completed.stdout}"
    )


def test_expected_lines_name_only_existing_examples() -> None:
    assert sorted(EXPECTED_LINES) == _examples()
    assert set(SLOW) <= set(EXPECTED_LINES)
