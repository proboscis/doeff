"""登記の前の入口 scripts/run_changed_tests.py(`make test-changed`)の失敗ケース(agora-redesign #2605 の 2 本目・1 便目)。

t135 の形: doeff-core-effects の `.hy` に公開の名を 1 つ足し、隣の `.pyi` を直さない commit は、書き手が選ぶ検
(test_sql_effects.hy)では緑のまま通り、契約の検 test_hy_module_stubs だけが赤にする。入口がその検を契約の組から選ぶので
赤で止まること、表の行を外すとその検は選ばれないこと(表が効いていること)、上限を越えた検と venv の無い作業木は
「未測」と名指して赤と分けること、表の形が違えば止めることを、一時の模型の repo で確かめる。

模型の repo は本物の file の写し(test_hy_module_stubs.py・doeff_core_effects の .hy と .pyi・executable ADR の pattern の
定義元)と、表だけを書いた pyproject.toml を持つ。模型は自分の環境を持たず、uv の UV_PROJECT_ENVIRONMENT で今の検の
環境を指す(入口は `uv run --no-sync --project <作業木>` で pytest を走らせる)。指さない時は「環境が無い作業木」になる。

2 便目(逆依存): 入口は doeff-linter の `--affected-tests` に問う。検では実の linter を呼ばず、決めた答えを返す代役の
命令(`--linter`)に差し替え、契約の組 → 変えた検 → 逆依存(距離の順)に並ぶこと・linter が答えない時に名指して進むことを
確かめる。linter 側の選び方そのものの失敗ケースは doeff-linter の Rust の検(project::affected_tests)が持つ。

session の境目(agora-redesign #2682): 入口は日次と同じ境目(repo の根・`packages/<名>/tests`・Makefile の
PACKAGE_EXTRA_TEST_ROOTS)ごとに別の pytest の process で走らせる。`tests/` に `__init__.py` と `conftest.py` を持つ package を
2 つ同時に選んでも止まらないこと(1 回の pytest では `tests.conftest` の名がぶつかり rc 2 で止まった)・合計の上限を束どうしで
分け合い、番の来なかった束を未測と名指すこと・束の並びが契約の組を先に保つことを確かめる。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "run_changed_tests.py"
STUB_TEST = "packages/doeff-core-effects/tests/test_hy_module_stubs.py"
CORE_EFFECTS = "packages/doeff-core-effects/doeff_core_effects"
ADR_PLUGIN = "packages/doeff-adr/src/doeff_adr/pytest_plugin.py"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "model",
    "GIT_AUTHOR_EMAIL": "model@example.invalid",
    "GIT_COMMITTER_NAME": "model",
    "GIT_COMMITTER_EMAIL": "model@example.invalid",
}


@dataclass(frozen=True)
class Row:
    """模型の表の 1 行(path の接頭辞と検の列)。"""

    prefix: str
    tests: tuple[str, ...]


@dataclass(frozen=True)
class Model:
    """模型の repo と、変える前の commit(入口の分岐点)と、空の答えを返す逆依存の代役(実の linter を呼ばない)。"""

    root: Path
    base: str
    linter: Path


#: 逆依存の答えの空の形(doeff-linter の `--affected-tests` の JSON)。
EMPTY_ANSWER = '{"tests": [], "hubs": [], "not_modules": [], "unreadable": [], "files_read": 0, "max_dependents": 40}'


@dataclass(frozen=True)
class RunnerOutput:
    returncode: int
    text: str
    stdout: str


def _git(root: Path, *args: str) -> str:
    """模型の repo で git を走らせる(作者は固定の名)。"""
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **GIT_ENV},
    )
    return proc.stdout.strip()


def _pyproject(rows: tuple[Row, ...]) -> str:
    """模型の pyproject.toml — pytest の rootdir の印と契約の検の表だけ。"""
    table = "".join(
        f'\n[[tool.doeff.contract-tests]]\nprefix = "{row.prefix}"\n'
        f"tests = [{', '.join(f'{t!r}' for t in row.tests)}]\n".replace("'", '"')
        for row in rows
    )
    return '[tool.pytest.ini_options]\nmarkers = ["e2e: model marker"]\n' + table


def _fake_linter(place: Path, answer: str, returncode: int = 0) -> Path:
    """逆依存の代役: 受けた引数を同じ dir の argv に 1 行ずつ書き、決めた答えを返す命令(模型の repo の外に置く — 変えた file に数えない)。"""
    place.mkdir(parents=True, exist_ok=True)
    answer_file = place / "answer.json"
    answer_file.write_text(answer, encoding="utf-8")
    script = place / "doeff-linter"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{place / "argv"}"\n'
        f'cat "{answer_file}"\n'
        "echo 'fake linter stderr' >&2\n"
        f"exit {returncode}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _make_model(
    tmp_path: Path, rows: tuple[Row, ...], extra: tuple[tuple[str, str], ...] = ()
) -> Model:
    """本物の契約の検と core-effects の .hy / .pyi を写した模型の repo を作り、1 つ目の commit を分岐点にする。

    extra = 分岐点に含める file(根からの path と中身)。逆依存の代役は空の答えを返す物を repo の外に置く。
    """
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copytree(
        REPO_ROOT / CORE_EFFECTS,
        root / CORE_EFFECTS,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    # Makefile は入口が session の境目(PACKAGE_EXTRA_TEST_ROOTS)を聞く定義元(agora-redesign #2682)。
    for rel in (STUB_TEST, ADR_PLUGIN, "Makefile"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, root / rel)
    (root / "tests").mkdir()
    (root / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    (root / "tests" / "test_slow.py").write_text(
        "import time\n\n\ndef test_slow():\n    time.sleep(60)\n", encoding="utf-8"
    )
    for rel, text in extra:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / "pyproject.toml").write_text(_pyproject(rows), encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return Model(
        root=root,
        base=_git(root, "rev-parse", "HEAD"),
        linter=_fake_linter(tmp_path / "linter-empty", EMPTY_ANSWER),
    )


def _add_public_name_without_stub(model: Model) -> None:
    """t135 の形の差分: sql_effects.hy に公開の名を 1 つ足し、sql_effects.pyi は直さずに commit する。"""
    module = model.root / CORE_EFFECTS / "sql_effects.hy"
    module.write_text(
        module.read_text(encoding="utf-8") + "\n(val ADDED-WITHOUT-STUB 1)\n", encoding="utf-8"
    )
    _git(model.root, "commit", "-q", "-am", "add a public name without its stub")


def _run(
    model: Model, *extra: str, with_environment: bool = True, linter: Path | None = None
) -> RunnerOutput:
    """入口を `make test-changed` と同じ `uv run --script` で模型の repo に対して走らせる(分岐点は模型の 1 つ目の commit)。

    with_environment = 模型の環境として今の検の環境(sys.prefix)を UV_PROJECT_ENVIRONMENT で指すか。
    linter = 逆依存の代役(既定は模型の空の答え — 実の doeff-linter は呼ばない)。
    """
    env = {
        k: v for k, v in os.environ.items() if k not in {"UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV"}
    }
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--script",
            str(RUNNER),
            "--repo",
            str(model.root),
            "--base",
            model.base,
            "--linter",
            str(linter if linter is not None else model.linter),
            *extra,
        ],
        env={**env, "UV_PROJECT_ENVIRONMENT": sys.prefix} if with_environment else env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return RunnerOutput(
        returncode=proc.returncode, text=proc.stdout + proc.stderr, stdout=proc.stdout
    )


def _counts(output: RunnerOutput) -> str:
    """入口の出力の最後の行 — 数だけの要約(agora-redesign #2685)。どの道で終わっても最後の行にちょうど 1 行出る。"""
    lines = [line for line in output.stdout.splitlines() if line.strip()]
    assert lines, f"入口が何も出していない(rc {output.returncode})\n{output.text[-3000:]}"
    assert lines[-1].startswith("land-focus-counts: "), lines[-5:]
    assert sum(1 for line in lines if line.startswith("land-focus-counts:")) == 1, lines
    return lines[-1]


def _summary(output: RunnerOutput) -> str:
    """入口の出力のうち、最後の要約の節(pytest の出力を除く)。"""
    marker = "== 変えた所の検の要約"
    assert marker in output.text, f"要約が無い(rc {output.returncode})\n{output.text[-3000:]}"
    return output.text[output.text.index(marker) :]


CORE_ROW = Row(prefix="packages/doeff-core-effects/", tests=(STUB_TEST,))
REPO_ROW = Row(prefix="", tests=("tests/test_ok.py",))


def test_a_hy_name_without_its_stub_is_red_through_the_contract_set(tmp_path: Path) -> None:
    """失敗ケース(t135): .hy に公開の名を足して .pyi を直さない差分は、契約の組から選ばれた stub の検で赤・rc 1。"""
    model = _make_model(tmp_path, (CORE_ROW, REPO_ROW))
    _add_public_name_without_stub(model)
    output = _run(model)
    summary = _summary(output)
    assert output.returncode == 1, f"赤なのに rc {output.returncode}\n{output.text[-3000:]}"
    assert "赤: 1" in summary, summary
    assert f"{STUB_TEST} — 赤" in summary, summary
    assert "[sql_effects]" in summary, summary
    assert "宣言に無い公開の名: ADDED_WITHOUT_STUB" in output.text, (
        "pytest の出力に足した名が出ていない"
    )
    assert "tests/test_ok.py — 1 本" in summary, summary
    assert "作業木の中身で走らせる(index ではない)" in output.text.splitlines()[0], output.text[
        :500
    ]


def test_the_stub_check_is_not_selected_without_its_table_row(tmp_path: Path) -> None:
    """表が効いている確かめ: core-effects の行を外すと、同じ差分でも stub の検は選ばれず、赤にならない。"""
    model = _make_model(tmp_path, (REPO_ROW,))
    _add_public_name_without_stub(model)
    output = _run(model)
    summary = _summary(output)
    assert STUB_TEST not in output.text, summary
    assert output.returncode == 0, output.text[-3000:]
    assert "走った: 1" in summary, summary
    assert "赤: 0" in summary, summary


def test_a_set_over_the_budget_is_named_unmeasured_not_red(tmp_path: Path) -> None:
    """上限(ここでは 8 秒)を越える組: 終わった検は「走った」、終わらなかった検は「未測」と名指し、rc は 0。"""
    model = _make_model(
        tmp_path, (Row(prefix="", tests=("tests/test_ok.py", "tests/test_slow.py")),)
    )
    (model.root / "README").write_text("changed\n", encoding="utf-8")
    output = _run(model, "--budget", "8")
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "tests/test_ok.py — 1 本" in summary, summary
    assert "未測: 1" in summary, summary
    assert "tests/test_slow.py — 上限 8 秒で打ち切り" in summary, summary
    assert "赤: 0" in summary, summary
    counts = _counts(output)
    assert " selected=2 ran=1 red=0 unmeasured=1 unmeasured_reverse=0 rc=0 " in counts, counts
    assert counts.endswith(" budget=8"), counts


def test_a_worktree_without_an_environment_names_every_selected_test_unmeasured(
    tmp_path: Path,
) -> None:
    """uv の環境(venv)の無い作業木: 黙って通さず、選んだ検の全部を「未測」と名指す(rc 0)。"""
    model = _make_model(tmp_path, (CORE_ROW, REPO_ROW))
    _add_public_name_without_stub(model)
    output = _run(model, with_environment=False)
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "未測: 2" in summary, summary
    assert "uv の環境" in summary, summary
    assert "が無い" in summary, summary
    assert STUB_TEST in summary, summary
    assert "tests/test_ok.py" in summary, summary


def test_a_table_naming_a_missing_test_file_stops(tmp_path: Path) -> None:
    """表に書いた検の file が木に無ければ止める(rc 2)— 書き損じた行を黙って空の組にしない。"""
    model = _make_model(tmp_path, (Row(prefix="", tests=("tests/test_gone.py",)),))
    output = _run(model)
    assert output.returncode == 2, output.text[-3000:]
    assert "tests/test_gone.py" in output.text, output.text
    # 選ぶ前に止まった回も数の要約を 1 行出す(選んだ検 0 本・rc 2)— 1 日の数から止まった回が漏れないように。
    assert " selected=0 ran=0 red=0 unmeasured=0 unmeasured_reverse=0 rc=2 " in _counts(output)


def test_the_repository_table_reads_and_names_existing_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本物の表は形どおりで、書いた検の file は全部木に在る(読み手は入口と同じ関数)。"""
    spec = importlib.util.spec_from_file_location("run_changed_tests", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    # dataclass は定義した module を sys.modules から引く — 検の間だけ置く。
    monkeypatch.setitem(sys.modules, spec.name, runner)
    spec.loader.exec_module(runner)
    table = runner.read_contract_table(REPO_ROOT)
    assert table.rows, "本物の表が空"
    assert all(
        (REPO_ROOT / test.split("::", 1)[0]).is_file() for row in table.rows for test in row.tests
    )


# ---------------------------------------------------------------------------
# 2 便目: 逆依存(doeff-linter の `--affected-tests`)の検を、契約の組と変えた検の後ろに距離の順で足す
# ---------------------------------------------------------------------------

PASSING = "def test_it():\n    pass\n"


def _affected(path: str, distance: int) -> dict[str, object]:
    """代役の答えの tests の 1 つ(道は変えた file から検までの 2 段で足りる)。"""
    return {"path": path, "distance": distance, "via": ["tests/test_changed.py", path]}


def test_reverse_dependents_follow_the_contract_set_and_the_changed_test_by_distance(
    tmp_path: Path,
) -> None:
    """並びの失敗ケース: 契約の組 → 変えた検 → 逆依存(距離の近い順・同じ距離は path の順)。代役の答えの順が乱れていても、
    重なり(契約の組に在る検)・検の file でない物・木に無い物を除いて決まった順に並べ、広すぎた module を名指す。
    linter には変えた file と、検の file の名の規則(集め手と同じ値)を渡す。"""
    model = _make_model(
        tmp_path,
        (REPO_ROW,),
        extra=(
            ("tests/test_near_a.py", PASSING),
            ("tests/test_near_b.py", PASSING),
            ("tests/test_far.py", PASSING),
            ("tests/helper.py", "X = 1\n"),
        ),
    )
    (model.root / "tests" / "test_changed.py").write_text(PASSING, encoding="utf-8")
    answer = {
        "tests": [
            _affected("tests/test_far.py", 2),
            _affected("tests/test_near_b.py", 1),
            _affected("tests/test_ok.py", 1),
            _affected("tests/helper.py", 1),
            _affected("tests/test_gone.py", 1),
            _affected("tests/test_near_a.py", 1),
        ],
        "hubs": [{"module": "macros", "root": "src", "dependents": 412, "distance": 0}],
        "not_modules": [],
        "unreadable": [],
        "files_read": 9,
        "max_dependents": 40,
    }
    linter = _fake_linter(tmp_path / "linter-answer", json.dumps(answer))
    output = _run(model, linter=linter)
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "走った: 5" in summary, summary
    ran = [line.strip().split(" — ", 1)[0] for line in summary.splitlines() if " — 1 本" in line]
    assert ran == [
        "tests/test_ok.py",
        "tests/test_changed.py",
        "tests/test_near_a.py",
        "tests/test_near_b.py",
        "tests/test_far.py",
    ], summary
    assert (
        "tests/test_far.py — 1 本(逆依存・距離 2: tests/test_changed.py → tests/test_far.py)"
        in summary
    )
    assert "逆依存: 足した検 3 本" in summary, summary
    assert "逆依存が広すぎて辿らなかった module: macros(根 src・直接の使い手 412)" in summary, (
        summary
    )
    argv = (linter.parent / "argv").read_text(encoding="utf-8").splitlines()
    assert argv[argv.index("--affected-tests") + 1] == "tests/test_changed.py", argv
    patterns = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--test-pattern"]
    assert patterns[:2] == ["test_*.py", "*_test.py"], argv
    assert "docs/adr/defadr_*.hy" in patterns, argv


@pytest.mark.parametrize(
    ("case", "answer", "returncode", "named"),
    [
        ("無い", None, 0, "が無い"),
        ("止まった", EMPTY_ANSWER, 2, "rc 2 で止まった — fake linter stderr"),
        ("読めない", "not json", 0, "linter の答えが読めない"),
        ("形が違う", '{"tests": [{"path": 1}], "hubs": [], "unreadable": []}', 0, "読めない"),
    ],
)
def test_an_unanswering_linter_is_named_and_the_run_goes_on(
    tmp_path: Path, case: str, answer: str | None, returncode: int, named: str
) -> None:
    """linter が答えない時: 逆依存を足さずに「逆依存を測れなかった」と理由つきで名指し、契約の組は走らせ、赤にしない(rc 0)。"""
    model = _make_model(tmp_path, (REPO_ROW,))
    (model.root / "README").write_text("changed\n", encoding="utf-8")
    linter = (
        tmp_path / "no-such-linter"
        if answer is None
        else _fake_linter(tmp_path / f"linter-{case}", answer, returncode)
    )
    output = _run(model, linter=linter)
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "tests/test_ok.py — 1 本" in summary, summary
    assert "赤: 0" in summary, summary
    assert "逆依存を測れなかった — " in summary, summary
    assert named in summary, summary


# ---------------------------------------------------------------------------
# 日次と同じ session の境目ごとに別の pytest の process(agora-redesign #2682)
# ---------------------------------------------------------------------------


def _package_tests(name: str) -> tuple[tuple[str, str], ...]:
    """`tests/` に `__init__.py` と `conftest.py` の両方を持つ package 1 つ(doeff の claude-code・cluster・conductor・records の形)。
    この形の package を 2 つ 1 つの pytest の session に混ぜると、どちらの conftest も `tests.conftest` の名になりぶつかる。"""
    base = f"packages/{name}/tests"
    return (
        (f"{base}/__init__.py", ""),
        (
            f"{base}/conftest.py",
            f"import pytest\n\n\n@pytest.fixture\ndef owner():\n    return {name!r}\n",
        ),
        (f"{base}/test_owner.py", f"def test_owner(owner):\n    assert owner == {name!r}\n"),
    )


def _touch_test(model: Model, rel: str) -> None:
    """模型の検の file に 1 行足す(その file を「変えた検」にする)。"""
    path = model.root / rel
    path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")


ALPHA = "packages/doeff-alpha/tests/test_owner.py"
BETA = "packages/doeff-beta/tests/test_owner.py"


def test_tests_of_two_packages_with_their_own_conftest_both_run(tmp_path: Path) -> None:
    """失敗ケース(#2658 の標本の 12 commit 中 6 本の形): `tests/__init__.py` と `conftest.py` を持つ package 2 つの検が同時に
    選ばれる変更。1 回の pytest に渡すと `tests.conftest` の名がぶつかり、入口は 1 本も走らせずに止まった(rc 2)。日次と
    同じ session の境目で process を分けるので、契約の組(repo の根)→ 2 つの package の順に 3 つの process で全部走る。"""
    model = _make_model(
        tmp_path, (REPO_ROW,), extra=(*_package_tests("doeff-alpha"), *_package_tests("doeff-beta"))
    )
    _touch_test(model, ALPHA)
    _touch_test(model, BETA)
    output = _run(model)
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "ImportPathMismatchError" not in output.text, output.text[-3000:]
    assert "走った: 3" in summary, summary
    assert f"{ALPHA} — 1 本(変えた検" in summary, summary
    assert f"{BETA} — 1 本(変えた検" in summary, summary
    assert (
        "pytest の process: 3/3 本が走った(session: ., packages/doeff-alpha/tests, packages/doeff-beta/tests)"
        in summary
    ), summary


def test_a_batch_left_without_budget_is_named_unmeasured(tmp_path: Path) -> None:
    """合計の上限は束どうしで分け合う: 先頭の契約の組の束(repo の根)が上限(ここでは 6 秒)を使い切ると、後ろの package の
    束は pytest を起こさず、その検を「番が来なかった」未測と名指す(赤にしない・rc 0)。"""
    model = _make_model(
        tmp_path,
        (Row(prefix="", tests=("tests/test_slow.py",)),),
        extra=_package_tests("doeff-alpha"),
    )
    _touch_test(model, ALPHA)
    output = _run(model, "--budget", "6")
    summary = _summary(output)
    assert output.returncode == 0, output.text[-3000:]
    assert "未測: 2" in summary, summary
    assert "tests/test_slow.py — 上限 6 秒で打ち切り" in summary, summary
    assert f"{ALPHA} — 上限 6 秒の内に番が来なかった" in summary, summary
    assert "pytest の process: 1/2 本が走った" in summary, summary


@pytest.fixture(scope="module")
def runner() -> Iterator[ModuleType]:
    """入口の script を module として読む(dataclass は定義した module を sys.modules から引く — 検の間だけ置く)。"""
    spec = importlib.util.spec_from_file_location("run_changed_tests", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        yield module


SIM = "packages/doeff-cluster/src/doeff_cluster/sim"


def test_each_test_goes_to_the_session_the_daily_run_uses(runner: ModuleType) -> None:
    """session の境目は日次と同じ: tests/ の外の根の下はその根・`packages/<名>/tests/` の下はその package・他は repo の根。
    `::検の名` の付いた引数も file の path で決める。"""
    roots = runner.SessionRoots(extra=(SIM,))
    assert runner.session_of(f"{SIM}/test_local.hy", roots) == SIM
    assert runner.session_of(f"{SIM}/deep/test_x.hy::test_y", roots) == SIM
    assert runner.session_of("packages/doeff-records/tests/test_laws.hy", roots) == (
        "packages/doeff-records/tests"
    )
    assert runner.session_of("packages/doeff-records/tests/sub/test_a.py::test_b", roots) == (
        "packages/doeff-records/tests"
    )
    assert runner.session_of("packages/doeff-records/src/doeff_records/test_x.py", roots) == "."
    assert runner.session_of("tests/test_daily_test_population.py", roots) == "."
    assert runner.session_of("docs/adr/defadr_doeff_domain_001.hy::test_x", roots) == "."
    # 根の名で始まるだけの別の dir は、その根ではない。
    assert runner.session_of(f"{SIM}_other/test_x.hy", roots) == "."


def test_batches_keep_the_contract_set_first_and_merge_neighbours(runner: ModuleType) -> None:
    """束の並び: 組(契約の組 → 変えた検 → 逆依存)の順を保ち、組の中は session ごとに束ねる(session の順は組の中で最初に
    現れた順)。隣り合う同じ session の束は 1 つの process にする — 契約の組が別の package に在っても、逆依存より先に走る。"""
    origin = runner.Origin

    def target(arg: str, kind: object) -> object:
        return runner.Target(arg=arg, origin=kind, why="検")

    selection = runner.Selection(
        targets=(
            target("tests/test_daily_test_population.py", origin.CONTRACT),
            target("packages/doeff-records/tests/test_static_stubs.py", origin.CONTRACT),
            target("packages/doeff-records/tests/test_changed.py", origin.CHANGED),
            target("packages/doeff-cluster/tests/test_near.py", origin.REVERSE),
            target("tests/test_far.py", origin.REVERSE),
            target("packages/doeff-cluster/tests/test_far.py", origin.REVERSE),
        )
    )
    batches = runner.batches_of(selection, runner.SessionRoots(extra=()))
    assert [(b.session, [t.arg for t in b.targets]) for b in batches] == [
        (".", ["tests/test_daily_test_population.py"]),
        (
            "packages/doeff-records/tests",
            [
                "packages/doeff-records/tests/test_static_stubs.py",
                "packages/doeff-records/tests/test_changed.py",
            ],
        ),
        (
            "packages/doeff-cluster/tests",
            [
                "packages/doeff-cluster/tests/test_near.py",
                "packages/doeff-cluster/tests/test_far.py",
            ],
        ),
        (".", ["tests/test_far.py"]),
    ]
    # 1 つの session だけなら 1 つの process(今までと同じ)。
    single = runner.Selection(targets=selection.targets[:1])
    assert len(runner.batches_of(single, runner.SessionRoots(extra=()))) == 1


def test_the_session_roots_are_read_from_the_makefile(runner: ModuleType) -> None:
    """本物の木: session の根は Makefile の PACKAGE_EXTRA_TEST_ROOTS(日次の母集団の定義元)から読め、どれも木に在る dir。"""
    roots = runner.read_session_roots(REPO_ROOT)
    assert roots.extra, "tests/ の外の根が 0 個 — Makefile の読みが壊れている"
    assert all((REPO_ROOT / root).is_dir() for root in roots.extra), roots


def test_the_counts_split_every_selected_test_once(runner: ModuleType) -> None:
    """数の要約(agora-redesign #2685): 選んだ検の 1 本ずつが走った・赤・未測のどれか 1 つに数えられ(selected = ran + red +
    unmeasured)、未測のうち逆依存の検を分けて数える。行は頭の印と `名=数` だけ(検の名の一覧は載せない)。"""
    origin, verdict = runner.Origin, runner.Verdict
    results = tuple(
        runner.TargetResult(runner.Target(arg=arg, origin=kind, why="検"), outcome, "理由")
        for arg, kind, outcome in (
            ("tests/test_contract.py", origin.CONTRACT, verdict.GREEN),
            ("tests/test_changed.py", origin.CHANGED, verdict.RED),
            ("tests/test_changed_slow.py", origin.CHANGED, verdict.UNMEASURED),
            ("tests/test_far_a.py", origin.REVERSE, verdict.UNMEASURED),
            ("tests/test_far_b.py", origin.REVERSE, verdict.UNMEASURED),
            ("tests/test_near.py", origin.REVERSE, verdict.GREEN),
        )
    )
    counts = runner.counts_of(runner.Finished(rc=1, results=results), 61.04, 60.0)
    assert (counts.selected, counts.ran, counts.red, counts.unmeasured) == (6, 2, 1, 3)
    assert counts.unmeasured_reverse == 2
    assert counts.selected == counts.ran + counts.red + counts.unmeasured
    line = runner.counts_line(counts)
    assert line == (
        "land-focus-counts: selected=6 ran=2 red=1 unmeasured=3 unmeasured_reverse=2"
        " rc=1 seconds=61.0 budget=60"
    ), line
    assert "tests/" not in line
    empty = runner.counts_line(runner.counts_of(runner.Finished(rc=2, results=()), 0.31, 60.0))
    assert empty == (
        "land-focus-counts: selected=0 ran=0 red=0 unmeasured=0 unmeasured_reverse=0"
        " rc=2 seconds=0.3 budget=60"
    ), empty
