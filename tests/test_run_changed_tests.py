"""登記の前の入口 scripts/run_changed_tests.py(`make test-changed`)の失敗ケース(agora-redesign #2605 の 2 本目・1 便目)。

t135 の形: doeff-core-effects の `.hy` に公開の名を 1 つ足し、隣の `.pyi` を直さない commit は、書き手が選ぶ検
(test_sql_effects.hy)では緑のまま通り、契約の検 test_hy_module_stubs だけが赤にする。入口がその検を契約の組から選ぶので
赤で止まること、表の行を外すとその検は選ばれないこと(表が効いていること)、上限を越えた検と venv の無い作業木は
「未測」と名指して赤と分けること、表の形が違えば止めることを、一時の模型の repo で確かめる。

模型の repo は本物の file の写し(test_hy_module_stubs.py・doeff_core_effects の .hy と .pyi・executable ADR の pattern の
定義元)と、表だけを書いた pyproject.toml を持つ。模型は自分の環境を持たず、uv の UV_PROJECT_ENVIRONMENT で今の検の
環境を指す(入口は `uv run --no-sync --project <作業木>` で pytest を走らせる)。指さない時は「環境が無い作業木」になる。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

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
    """模型の repo と、変える前の commit(入口の分岐点)。"""

    root: Path
    base: str


@dataclass(frozen=True)
class RunnerOutput:
    returncode: int
    text: str


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


def _make_model(root: Path, rows: tuple[Row, ...]) -> Model:
    """本物の契約の検と core-effects の .hy / .pyi を写した模型の repo を作り、1 つ目の commit を分岐点にする。"""
    shutil.copytree(
        REPO_ROOT / CORE_EFFECTS,
        root / CORE_EFFECTS,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for rel in (STUB_TEST, ADR_PLUGIN):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, root / rel)
    (root / "tests").mkdir()
    (root / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    (root / "tests" / "test_slow.py").write_text(
        "import time\n\n\ndef test_slow():\n    time.sleep(60)\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(_pyproject(rows), encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return Model(root=root, base=_git(root, "rev-parse", "HEAD"))


def _add_public_name_without_stub(model: Model) -> None:
    """t135 の形の差分: sql_effects.hy に公開の名を 1 つ足し、sql_effects.pyi は直さずに commit する。"""
    module = model.root / CORE_EFFECTS / "sql_effects.hy"
    module.write_text(
        module.read_text(encoding="utf-8") + "\n(val ADDED-WITHOUT-STUB 1)\n", encoding="utf-8"
    )
    _git(model.root, "commit", "-q", "-am", "add a public name without its stub")


def _run(model: Model, *extra: str, with_environment: bool = True) -> RunnerOutput:
    """入口を `make test-changed` と同じ `uv run --script` で模型の repo に対して走らせる(分岐点は模型の 1 つ目の commit)。

    with_environment = 模型の環境として今の検の環境(sys.prefix)を UV_PROJECT_ENVIRONMENT で指すか。
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
            *extra,
        ],
        env={**env, "UV_PROJECT_ENVIRONMENT": sys.prefix} if with_environment else env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return RunnerOutput(returncode=proc.returncode, text=proc.stdout + proc.stderr)


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
