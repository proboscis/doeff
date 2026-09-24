"""ADR-DOE-ENFORCE-001 R8: 日次の全体検証の母集団の網羅・失敗名の形・test file の置き場。

日次の全体検証(.agents/land-queue.toml の gate.full)の母集団は root の pytest・package ごとの
pytest(`make test-packages`)・Rust crate ごとの cargo test(`make test-rust`)の 3 つ。宣言の形
(母集団ごとに別の処理ステージ)は ADR の deftest
test-adr-doe-enforce-001-daily-populations-are-separate-stages が持ち、ここは挙動の本体を持つ:

- package の loop は期待の集合を全部訪ね、赤の後も続け、最後に失敗の package を名指す。
- package の失敗名は repo の根からの相対で、package をまたいで衝突しない(日次の道具は失敗名を
  要約の行から逐語で取り、repo の根から pytest へそのまま渡す)。
- test 名の file はどれかの母集団の根の下か、理由つきの除外の表に在る。
- package は自分の pytest の設定を持たない(持つと root の ini と conftest が効かなくなる)。

反例の実弾: 2026-09-24 の日次(断面 f271ae39)は root の赤 3 本で `&&` が止まり、package と
Rust の母集団は 1 本も走らないまま台帳は coverage = complete と記録した。`make test-packages`
の loop も最初の赤の package で止まり(`|| exit 1`)、package の dir から走らせた失敗名は
`tests/test_cli.py::…` の形で 4 組の同名 file が衝突した(docs/design/test-population-ki-08ec/)。
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]

# pytest の既定の収集規則(root の ini は python_files を変えていない)。
_TEST_FILE_NAME = re.compile(r"(^|/)(test_[^/]*|[^/]*_test)\.py$")

# 歩かない dir = pytest の既定の norecursedirs + build の出力(target・__pycache__)。
# 日次の遠隔の検査の木には .git が無く(remote_check は git の名簿の file だけを送る)、
# make sync の出力(.venv・target)が在るので、git ではなく file system を歩く。
# 2026-09-25 実測: この規則で歩いた test 名の file の集合は、素の worktree(450 本)でも
# build 済みの checkout(449 本)でも `git ls-files --cached --others --exclude-standard` と一致した。
_NOT_WALKED = (
    "*.egg",
    "*.egg-info",
    ".*",
    "_darcs",
    "build",
    "CVS",
    "dist",
    "node_modules",
    "venv",
    "{arch}",
    "target",
    "__pycache__",
)

# どの母集団にも属さない test 名の file の除外の表(path か、`/` で終わる dir の接頭辞 → 理由)。
# 行を足す時は理由を書く。該当する file が 1 本も無い行は古い行として赤になる。
EXCLUDED: dict[str, str] = {
    "packages/doeff-agents/conformance/": (
        "tmux / herdr の実 pane を使う黒箱の交代ゲート(実モデルは使わない)。日次の宿で tmux が"
        "使えるかが未測定で、母集団へ入れるかは card acp:kanban-issue:ki-2832a04913cc が決める"
    ),
    "docs/design/": "設計の検証の模型と実験(その時点の記録で、日次の母集団ではない)",
    "ide-plugins/pycharm/test_program_detection.py": (
        "PyCharm plugin の検出の検体(テスト関数を持たない)"
    ),
    "packages/doeff-agentic/examples/": "例示の script(__main__ で走らせる)",
    "packages/doeff-test-target/src/doeff_test_target/effects/test_effects.py": (
        "fixture の module 名(テストではない)"
    ),
    "tools/test_python_versions.py": "Python の版を回す道具の script",
}


def _expected_packages(repo: Path) -> list[str]:
    """package の母集団の期待 — `packages/<p>/tests` の下に fixtures 以外の test_*.py が在る p の全部。

    Makefile の実走からは取らない: 期待を実装から取ると、Makefile が母集団を縮めた時に
    訪ねた集合と期待が一緒に縮んで緑のまま残る(盲検 B の反例)。
    """
    return sorted(
        tests_dir.parent.name
        for tests_dir in (repo / "packages").glob("*/tests")
        if tests_dir.is_dir()
        and any(
            "fixtures" not in path.relative_to(tests_dir).parts
            for path in tests_dir.rglob("test_*.py")
        )
    )


def _test_named_files(repo: Path) -> list[str]:
    """repo の test 名の file(fixtures の下を除く)の repo の根からの相対 path。"""
    found: list[str] = []
    for current, dirs, files in os.walk(repo):
        dirs[:] = sorted(
            name
            for name in dirs
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in _NOT_WALKED)
        )
        for name in files:
            rel = Path(current, name).relative_to(repo).as_posix()
            if _TEST_FILE_NAME.search(rel) and "fixtures" not in rel.split("/"):
                found.append(rel)
    return sorted(found)


def _is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _is_excluded(path: str, row: str) -> bool:
    return path == row or (row.endswith("/") and path.startswith(row))


def _root_testpaths(repo: Path) -> list[str]:
    config = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    return list(config["tool"]["pytest"]["ini_options"]["testpaths"])


def _named_failed_packages(output: str) -> list[str] | None:
    """`make test-packages` が最後に名指した失敗の package(`test-packages failed:` の行が無ければ None)。"""
    summary = [line for line in output.splitlines() if line.startswith("test-packages failed:")]
    return summary[-1].split(":", 1)[1].split() if summary else None


_FAKE_RUNNER = """\
import json, os, sys
from pathlib import Path

with open(os.environ["DOEFF_FAKE_RUNNER_RECORD"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"cwd": os.getcwd(), "argv": sys.argv[1:]}) + "\\n")
fail = Path(os.environ["DOEFF_FAKE_RUNNER_FAIL"]).resolve()
sys.exit(1 if any(Path(arg).resolve() == fail for arg in sys.argv[1:]) else 0)
"""


def test_make_test_packages_visits_every_package_after_a_red(tmp_path: Path) -> None:
    """期待の集合の名前順で最初の package だけを赤にしても、loop は全部を訪ねて最後に名指す。

    偽の runner は受け取った引数を記録し、最初の package の tests を受けた時だけ 1 を返す
    (引数は runner の cwd から解くので、package の dir から走らせる形でも同じ package と数える)。
    """
    expected = _expected_packages(REPO_ROOT)
    assert expected, "packages/*/tests に Python のテストを持つ package が 1 つも見つからない"
    first = expected[0]
    runner = tmp_path / "fake_runner.py"
    runner.write_text(_FAKE_RUNNER, encoding="utf-8")
    record = tmp_path / "calls.jsonl"
    env = {
        **os.environ,
        "DOEFF_FAKE_RUNNER_RECORD": str(record),
        "DOEFF_FAKE_RUNNER_FAIL": str(REPO_ROOT / "packages" / first / "tests"),
    }
    proc = subprocess.run(
        [
            "make",
            "-s",
            "-C",
            str(REPO_ROOT),
            "test-packages",
            f"PACKAGE_UV_RUN={sys.executable} {runner}",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = proc.stdout + proc.stderr
    calls = (
        [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
        if record.exists()
        else []
    )

    packages_dir = (REPO_ROOT / "packages").resolve()
    reached: set[str] = set()
    for call in calls:
        for arg in call["argv"]:
            resolved = Path(call["cwd"], arg).resolve()
            if resolved.parent.parent == packages_dir and resolved.name == "tests":
                reached.add(resolved.parent.name)

    assert reached == set(expected), (
        f"make test-packages が訪ねた package が期待と違う: 欠け {sorted(set(expected) - reached)}"
        f" / 余り {sorted(reached - set(expected))}(runner が呼ばれた回数 {len(calls)}・"
        f"最初の {first} だけを赤にした)— 欠けた package は日次で 1 本も走らない(赤の後で"
        f"止めた・または母集団から外した)— ADR-DOE-ENFORCE-001 R8\n{output[-2000:]}"
    )
    assert proc.returncode != 0, (
        f"最初の package {first} を赤にしたのに make test-packages の rc が 0 — R8\n{output[-2000:]}"
    )
    named = _named_failed_packages(output)
    assert named == [first], (
        f"最後に失敗の package ({first}) だけを `test-packages failed:` の行で名指していない: "
        f"{named} — R8\n{output[-2000:]}"
    )


def test_package_failure_names_are_repo_root_relative(tmp_path: Path) -> None:
    """模型の木で本物の Makefile と本物の pytest を走らせ、失敗名が repo の根からの相対であることを見る。

    package a・b が同名の失敗 tests/test_cli.py::test_fail を持ち、c は緑。package の dir から
    走らせる形では 2 つとも `FAILED tests/test_cli.py::test_fail` になって衝突する。
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nmarkers = ["e2e: model marker"]\n', encoding="utf-8"
    )
    for package in ("a", "b"):
        test_file = tmp_path / "packages" / package / "tests" / "test_cli.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def test_fail():\n    assert False\n", encoding="utf-8")
    green = tmp_path / "packages" / "c" / "tests" / "test_ok.py"
    green.parent.mkdir(parents=True)
    green.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    # 模型の木は root の conftest も外の plugin も持たない — 外の走行の設定を持ち込まない。
    env = {
        **os.environ,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_ADDOPTS": "-p no:cacheprovider",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proc = subprocess.run(
        [
            "make",
            "-s",
            "-f",
            str(REPO_ROOT / "Makefile"),
            "-C",
            str(tmp_path),
            "test-packages",
            f"PACKAGE_UV_RUN={sys.executable} -m",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = proc.stdout + proc.stderr
    lines = output.splitlines()
    failed = sorted(line.split(" - ")[0] for line in lines if line.startswith("FAILED "))

    assert failed == [
        "FAILED packages/a/tests/test_cli.py::test_fail",
        "FAILED packages/b/tests/test_cli.py::test_fail",
    ], (
        f"package の失敗名が repo の根からの相対でない / package をまたいで衝突する: {failed}"
        f" — 日次の道具は失敗名を repo の根から pytest へ渡す — ADR-DOE-ENFORCE-001 R8\n{output}"
    )
    assert any("packages/c/tests/test_ok.py" in line for line in lines), (
        f"赤の後ろの緑の package c が走っていない — R8\n{output}"
    )
    assert proc.returncode != 0, f"失敗の package があるのに rc が 0 — R8\n{output}"
    named = _named_failed_packages(output)
    assert named == ["a", "b"], (
        f"最後に失敗の package (a b) を `test-packages failed:` の行で名指していない: {named}"
        f" — R8\n{output}"
    )


def test_every_test_file_belongs_to_a_daily_population() -> None:
    """test 名の file は root の testpaths か package の母集団の根の下か、除外の表に在る。"""
    population_roots = [
        *_root_testpaths(REPO_ROOT),
        *(f"packages/{package}/tests" for package in _expected_packages(REPO_ROOT)),
    ]
    orphans = [
        path
        for path in _test_named_files(REPO_ROOT)
        if not any(_is_under(path, root) for root in population_roots)
        and not any(_is_excluded(path, row) for row in EXCLUDED)
    ]
    assert not orphans, (
        f"日次のどの母集団にも除外の表にも無い test 名の file が {len(orphans)} 本: {orphans}"
        " — 母集団の根の下へ置くか、tests/test_daily_test_population.py の EXCLUDED に理由つきで"
        "載せる(呼び手の無い木の緑は日次に見えない)— ADR-DOE-ENFORCE-001 R8"
    )


def test_exclusion_table_has_no_stale_rows() -> None:
    """除外の表の各行は、今の木の test 名の file を 1 本以上覆う。"""
    files = _test_named_files(REPO_ROOT)
    stale = [row for row in EXCLUDED if not any(_is_excluded(path, row) for path in files)]
    assert not stale, (
        f"除外の表に該当する file が 1 本も無い古い行: {stale} — 消す — ADR-DOE-ENFORCE-001 R8"
    )


def _declares_pytest_config(directory: Path) -> str | None:
    """pytest の rootdir の決め方で ini と読まれる設定を directory が持つなら、その file の名前。"""
    for name in ("pytest.ini", ".pytest.ini"):
        if (directory / name).is_file():
            return name
    pyproject = directory / "pyproject.toml"
    if pyproject.is_file():
        tool = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("tool", {})
        if "pytest" in tool:
            return "pyproject.toml [tool.pytest]"
    tox = directory / "tox.ini"
    if tox.is_file() and re.search(r"^\[pytest\]", tox.read_text(encoding="utf-8"), re.M):
        return "tox.ini [pytest]"
    setup_cfg = directory / "setup.cfg"
    if setup_cfg.is_file() and re.search(
        r"^\[tool:pytest\]", setup_cfg.read_text(encoding="utf-8"), re.M
    ):
        return "setup.cfg [tool:pytest]"
    return None


def test_no_package_declares_its_own_pytest_ini() -> None:
    """`pytest packages/<p>/tests` の rootdir は repo の根で、root の ini と conftest が効く。

    package(やその tests)が自分の pytest の設定を持つと rootdir がそこへ移り、root の ini
    (markers・timeout 等)と root の conftest(締切・受付)が package の母集団に効かなくなり、
    失敗名も repo の根からの相対でなくなる(盲検 A の副次)。
    """
    declared = {
        str(directory.relative_to(REPO_ROOT)): found
        for package in _expected_packages(REPO_ROOT)
        for directory in (
            REPO_ROOT / "packages",
            REPO_ROOT / "packages" / package,
            REPO_ROOT / "packages" / package / "tests",
        )
        if (found := _declares_pytest_config(directory)) is not None
    }
    assert not declared, (
        f"package の母集団の path に自分の pytest の設定が在る: {declared}"
        " — root の pyproject へ寄せる — ADR-DOE-ENFORCE-001 R8"
    )
