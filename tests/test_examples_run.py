"""examples/ の例の file が今の doeff で script として最後まで走り、書いた通りの結果を印字する事の検(取り組み doeff-publish・card ki-524874f8945a)。

examples/ の例は利用者が写して走らせる読み物で、どの検も走らせていなかったので、API が変わっても誰も気づかなかった:
`Program.of`・`ask`・`default_handlers`・`async_run` が消えた後も、5 file 全部が import か実行の初めで落ちていた
(docs の報告 2026-10-17-doeff-docs-examples-run.html の「古い使い方の訳」)。

examples/ の直下の `.py` は全部この検に入る(新しい例も書いただけで入り、EXPECTED_LINES に行が無ければ赤になる)。
終了コードだけでは足りない: traceback_demo は失敗を起こして捕まえる例なので、起こしたはずの失敗が起きなくても 0 で終わる。
そこで例ごとに、標準出力に必ず出る行を EXPECTED_LINES に置き、全部が出る事を確かめる。例の印字を変えたら、ここも直す。
例は開発の環境で走らせる(llm_multi_provider_stacking は核の外の部品 doeff-gemini・doeff-llm・doeff-openai・
doeff-openrouter を使う)。核の 5 個だけの新しい環境で走る事は、card の発言に結果を書いて確かめた。
核の外の部品を import する例は NEEDS_OUTSIDE_CORE に import 名を置く: 部品が入っていない環境(核の 5 個だけを入れる CI の
Python Compatibility の venv — .github/workflows/python-compatibility.yml)では skip になり、入っている開発の環境では走る
(skip しないと、例の import が ModuleNotFoundError で落ちて main の CI 全体が赤になった — 2026-10-17)。
1 本が長い例は SLOW に訳つきで置き、印 slow を付ける(`make test` の `-m "not e2e and not slow"` が外す — 名指せば走る)。

名が test_ で始まる `.py` は、例を確かめる pytest の検の例(llm_agent.py の test_llm_agent.py — 取り組み doeff-publish・card の
依頼 lt-XNZW7KVT5ZHV9MKYY8NCWGN69S)で、script としては走らせない。利用者は例の file を写して `pytest test_<名>.py` と打つので、
examples/ の `.py` だけを repo の外の dir に写して pytest で走らせ、全部が通る事を見る(repo の conftest.py と pyproject.toml の
pytest の設定を読ませない)。例の検は LLM の model の key も network も使わない約束なので、OPENAI_ の環境変数を外して走らせる。

dir に分けた例 examples/github_repo/(doeff[http] の handler で GitHub の API を呼ぶ program と、偽の handler の pytest の検の例 —
card ki-1356963e2d0b)は、script を走らせると本物の network へ出るので、直下の例の検に入れない。下の 2 本が受け持つ:
script が本番で入れる handler の組(live — http_production_handler)を手元に立てた偽の GitHub API へ向けて子で走らせる検
(例の検は live を走らせないので、これが無いと http の handler の使い方が変わっても例の本番の道が落ちる事に誰も気づかない)と、
README の命令 `pytest examples/github_repo` を pytest の外の plugin 無しの子で走らせる検(例の dir の pytest.ini が rootdir を
例の dir に留め、repo の根の conftest を読ませない — clone して `pip install "doeff[http]" pytest` だけを入れた人の形)。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.cli.cli_child import with_settings

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
# 子 1 つの制限時間の、負荷で伸ばす前の値(秒)。1 本は 約 1〜3 秒(多くは doeff の import)。
CHILD_DEADLINE_SECONDS = 50

# 例ごとの、標準出力に出るはずの行(行の全体で比べる)。結果の値を印字する行を選ぶ。
EXPECTED_LINES: dict[str, tuple[str, ...]] = {
    "llm_agent.py": (
        "question: Should a small team build its own job queue?",
        "answer: No: an existing queue already does all of it, for little cost.",
    ),
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
        "caught MissingEnvKeyError — run() printed its doeff traceback above",
        "caught ValueError — run() printed its doeff traceback above",
    ),
}


# 1 本が長い例と、その訳。
SLOW = {
    "llm_multi_provider_stacking.py": "openai の SDK の import だけで 約 25 秒かかる(2026-10-17 の開発の環境・Python 3.14t)",
}


# 核の外の部品を import する例と、その部品の import 名。
NEEDS_OUTSIDE_CORE = {
    "llm_multi_provider_stacking.py": ("doeff_gemini", "doeff_llm", "doeff_openai", "doeff_openrouter"),
}


def _examples() -> list[str]:
    """script として走らせる例(test_ で始まる名は pytest の検の例なので外す)。"""
    return sorted(path.name for path in EXAMPLES.glob("*.py") if not path.name.startswith("test_"))


def _example_tests() -> list[str]:
    """例を確かめる pytest の検の例。"""
    return sorted(path.name for path in EXAMPLES.glob("test_*.py"))


def _marks(name: str) -> tuple[pytest.MarkDecorator, ...]:
    # find_spec は部品を読み込まない(openai の SDK の import に 約 25 秒かかるので、探すだけにする)。
    absent = tuple(m for m in NEEDS_OUTSIDE_CORE.get(name, ()) if importlib.util.find_spec(m) is None)
    slow = (pytest.mark.slow,) if name in SLOW else ()
    skip = (pytest.mark.skip(reason=f"核の外の部品 {', '.join(absent)} がこの環境に入っていない"),) if absent else ()
    return slow + skip


def _params() -> list[object]:
    return [pytest.param(name, marks=_marks(name)) for name in _examples()]


@pytest.mark.parametrize("name", _params())
def test_example_runs_and_prints_its_results(name: str, deadline_scale_factor: float) -> None:
    assert name in EXPECTED_LINES, (
        f"examples/{name} の標準出力に出るはずの行を、tests/test_examples_run.py の EXPECTED_LINES に置く"
    )
    completed = subprocess.run(
        with_settings([sys.executable, str(EXAMPLES / name)], {"DOEFF_PROFILE": None}),
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


@pytest.mark.parametrize("name", _example_tests())
def test_example_tests_pass_with_only_the_example_files(
    name: str, tmp_path: Path, deadline_scale_factor: float
) -> None:
    # 利用者が例の file を写した dir で `pytest test_<名>.py` と打つのと同じ形で、検の例が通るかを見る。
    for path in EXAMPLES.glob("*.py"):
        shutil.copy(path, tmp_path / path.name)
    no_keys = {variable: None for variable in os.environ if variable.startswith("OPENAI_")}
    completed = subprocess.run(
        with_settings(
            [sys.executable, "-m", "pytest", "-q", name], {"DOEFF_PROFILE": None, **no_keys}
        ),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )
    assert completed.returncode == 0, (
        f"examples/{name} の pytest が終了コード {completed.returncode} で落ちた:\n"
        f"{completed.stdout[-3000:]}{completed.stderr[-3000:]}"
    )
    lines = completed.stdout.splitlines()
    assert lines, f"examples/{name} の pytest が何も印字しなかった:\n{completed.stderr[-3000:]}"
    assert re.match(r"\d+ passed", lines[-1]), (
        f"examples/{name} の pytest の要約の行が passed で始まらない: {lines[-1]!r}"
    )


def test_expected_lines_name_only_existing_examples() -> None:
    assert sorted(EXPECTED_LINES) == _examples()
    assert set(SLOW) <= set(EXPECTED_LINES)
    assert set(NEEDS_OUTSIDE_CORE) <= set(EXPECTED_LINES)


GITHUB_REPO_EXAMPLE = EXAMPLES / "github_repo" / "repo_summary.py"
# 手元の偽の GitHub API が答える path と JSON の本文(無い path には 404 の Not Found)。
FAKE_GITHUB_API: dict[str, object] = {
    "/repos/octo/hello": {
        "full_name": "octo/hello",
        "description": "Says hello",
        "stargazers_count": 42,
        "forks_count": 7,
        "open_issues_count": 3,
    },
    "/repos/octo/hello/releases/latest": {"tag_name": "v1.2.0"},
}


class _FakeGitHubApi(BaseHTTPRequestHandler):
    """FAKE_GITHUB_API の本文を JSON で返す、127.0.0.1 だけで答える GitHub API の替え玉。"""

    def do_GET(self) -> None:
        found = self.path in FAKE_GITHUB_API
        body = FAKE_GITHUB_API[self.path] if found else {"message": "Not Found"}
        payload = json.dumps(body).encode()
        self.send_response(200 if found else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:  # 要求ごとの access log を検の stderr に出さない
        return None


def test_github_repo_example_runs_its_live_handlers_against_a_local_api(
    deadline_scale_factor: float,
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGitHubApi)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    api = f"http://127.0.0.1:{server.server_port}"
    try:
        # httpx は proxy の env を読むので、127.0.0.1 を proxy の外に置く(proxy の在る機体でも手元の API へ直に行く)。
        completed = subprocess.run(
            with_settings(
                [sys.executable, str(GITHUB_REPO_EXAMPLE), "octo/hello", "--api", api],
                {"DOEFF_PROFILE": None, "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"},
            ),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert completed.returncode == 0, (
        f"examples/github_repo/repo_summary.py が終了コード {completed.returncode} で落ちた:\n"
        f"{completed.stderr[-3000:]}"
    )
    assert completed.stdout.splitlines() == [
        "octo/hello: Says hello",
        "42 stars, 7 forks, 3 open issues and pull requests, latest release: v1.2.0",
    ]


def test_github_repo_example_tests_run_with_pytest_alone(deadline_scale_factor: float) -> None:
    # README が書く `pytest examples/github_repo` を、doeff を clone して `pip install "doeff[http]" pytest` だけを入れた人と
    # 同じく、pytest の外の plugin 無しで走らせる。例の dir の pytest.ini が rootdir を例の dir に留めるので、repo の根の
    # conftest.py(締切に pytest-timeout などの開発の道具を要る)を読まない — pytest.ini が無いと、根の conftest が
    # `no option named 'timeout'` の INTERNALERROR で止まった(2026-10-17・PyPI の 0.6.0 と pytest だけの環境でも同じ)。
    pytest_alone = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    completed = subprocess.run(
        with_settings(
            [*pytest_alone, "examples/github_repo"],
            {"DOEFF_PROFILE": None, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": None},
        ),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )
    assert completed.returncode == 0, (
        f"pytest examples/github_repo が終了コード {completed.returncode} で落ちた:\n"
        f"{completed.stdout[-3000:]}\n{completed.stderr[-3000:]}"
    )
    summary = completed.stdout.splitlines()[-1]  # -q の最後の行: `4 passed in …s`
    assert summary.startswith("4 passed"), completed.stdout
