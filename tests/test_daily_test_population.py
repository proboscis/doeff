"""ADR-DOE-ENFORCE-001 R8: 日次の全体検証の母集団の網羅・失敗名の形・test file の置き場。

日次の全体検証(.agents/land-queue.toml の gate.full)の母集団は root の pytest・package ごとの
pytest(`make test-packages`)・Rust crate ごとの cargo test(`make test-rust`)の 3 つ。宣言の形
(母集団ごとに別の処理ステージ)は ADR の deftest
test-adr-doe-enforce-001-daily-populations-are-separate-stages が持ち、ここは挙動の本体を持つ:

- package の loop は期待の集合と package の tests/ の外の根(Makefile の PACKAGE_EXTRA_TEST_ROOTS)を
  全部訪ね、赤の後も続け、最後に失敗の package を名指す。
- package の失敗名は repo の根からの相対で、package をまたいで衝突しない(日次の道具は失敗名を
  要約の行から逐語で取り、repo の根から pytest へそのまま渡す)。
- 検の file(`.py` と `.hy` — 定義は `_test_file_patterns` の 1 点)はどれかの母集団の根の下か、
  理由つきの除外の表に在る。
- 検のつもりの名(test_*.py・test_*.hy)の file は、どれかの集め手の pattern(root の ini の python_files・
  doeff_hy_test_files・executable ADR の pattern)に当たり、Hy の集め手を conftest.py に写さない。外す一覧
  (doeff_hy_test_skips)の行は今の Hy の検の file を名指す(agora-redesign #2591)。
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
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import tomllib
from doeff_adr.pytest_plugin import DEFAULT_FILE_PATTERNS as ADR_FILE_PATTERNS
from doeff_adr.pytest_plugin import parse_hy_test_skips
from doeff_core_effects.os_process import subprocess_handler
from doeff_core_effects.process_effects import EnvEntry, EnvMode, RunProcess

from doeff import run, with_handlers

REPO_ROOT = Path(__file__).resolve().parents[1]

# pytest の既定の python_files(root の ini が python_files を書けば、そちらを読む)。
_PYTEST_PYTHON_FILES = ("test_*.py", "*_test.py")
# 「検のつもりの名」の file — どれかの集め手に集められなければ、日次で 1 本も走らない(agora-redesign #2591)。
_TEST_NAMED_SOURCES = ("test_*.py", "test_*.hy")


def _test_file_patterns(repo: Path) -> tuple[str, ...]:
    """「何が検の file か」の定義の 1 点 — pytest が集める 3 つの経路の名の規則。どれも集め手そのものが読む値。

    - Python の検: root の ini の python_files(既定は pytest の既定)。
    - Hy の検の file: root の ini の doeff_hy_test_files(doeff-adr の plugin の集め手が同じ値を読む・deftest の
      file も上から順に実行する script も集める — agora-redesign #2591)。
    - executable ADR: doeff-adr の plugin の既定の pattern と root の ini の doeff_adr_hy_files。

    `.py` だけを数えていた時は、母集団の外の `.hy` の検が日次で走らなくても赤にならなかった
    (#2542 の packages/doeff-cluster/src/doeff_cluster/sim/test_entries_on_sim.hy・agora-redesign #2577)。
    Hy の条件を検の側に `test_*.hy` と写していた時は、集める conftest の無い dir の test_*.hy(packages/doeff-hy/tests
    の 6 本)を「検の file」と数えながら、どの集め手も集めていなかった(agora-redesign #2591)。
    """
    ini = _root_ini(repo)
    return (
        *ini.get("python_files", _PYTEST_PYTHON_FILES),
        *ini.get("doeff_hy_test_files", ()),
        *ADR_FILE_PATTERNS,
        *ini.get("doeff_adr_hy_files", ()),
    )


def _is_test_file(rel: str, patterns: tuple[str, ...]) -> bool:
    """pytest の照合と同じ: `/` を含まない pattern は file の名に、含む pattern は repo の根からの path に当てる。"""
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(rel if "/" in p else name, p) for p in patterns)


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
    """package の母集団の期待 — `packages/<p>/tests` の下に fixtures 以外の検の file が在る p の全部。

    Makefile の実走からは取らない: 期待を実装から取ると、Makefile が母集団を縮めた時に
    訪ねた集合と期待が一緒に縮んで緑のまま残る(盲検 B の反例)。
    """
    files = _test_named_files(repo)
    return sorted(
        {
            parts[1]
            for parts in (path.split("/") for path in files)
            if len(parts) > 3 and parts[0] == "packages" and parts[2] == "tests"
        }
    )


def _test_named_files(repo: Path) -> list[str]:
    """repo の検の file(`_test_file_patterns` に当たる・fixtures の下を除く)の repo の根からの相対 path。"""
    patterns = _test_file_patterns(repo)
    return sorted(
        rel
        for current, dirs, files in _walk(repo)
        for rel in (Path(current, name).relative_to(repo).as_posix() for name in files)
        if _is_test_file(rel, patterns) and "fixtures" not in rel.split("/")
    )


def _walk(repo: Path) -> Iterator[tuple[str, list[str], list[str]]]:
    """`_NOT_WALKED` の dir へ降りない os.walk。"""
    for current, dirs, files in os.walk(repo):
        dirs[:] = sorted(
            name
            for name in dirs
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in _NOT_WALKED)
        )
        yield current, dirs, files


def _is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _is_excluded(path: str, row: str) -> bool:
    return path == row or (row.endswith("/") and path.startswith(row))


def _root_ini(repo: Path) -> dict[str, Any]:
    config = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["pytest"]["ini_options"]


def _root_testpaths(repo: Path) -> list[str]:
    return list(_root_ini(repo)["testpaths"])


def _package_extra_test_roots(repo: Path, overrides: tuple[str, ...] = ()) -> list[str]:
    """`make test-packages` が package の tests/ の外で走らせる根 — 定義は Makefile の PACKAGE_EXTRA_TEST_ROOTS の 1 点。

    検の側に一覧を写さず、本物の Makefile に名を聞く(overrides は make の命令行の変数の上書き)。
    """
    proc = subprocess.run(
        [
            "make",
            "-s",
            "--no-print-directory",
            "-f",
            str(REPO_ROOT / "Makefile"),
            "-C",
            str(repo),
            "print-package-extra-test-roots",
            *overrides,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return proc.stdout.split()


def _population_roots(repo: Path, overrides: tuple[str, ...] = ()) -> list[str]:
    """日次の母集団の根 — root の testpaths・package ごとの tests・package の tests の外の根。"""
    return [
        *_root_testpaths(repo),
        *(f"packages/{package}/tests" for package in _expected_packages(repo)),
        *_package_extra_test_roots(repo, overrides),
    ]


def _assert_every_test_file_belongs_to_a_daily_population(repo: Path, roots: list[str]) -> None:
    orphans = [
        path
        for path in _test_named_files(repo)
        if not any(_is_under(path, root) for root in roots)
        and not any(_is_excluded(path, row) for row in EXCLUDED)
    ]
    assert not orphans, (
        f"日次のどの母集団にも除外の表にも無い検の file が {len(orphans)} 本: {orphans}"
        " — 母集団の根の下へ置くか(package の tests/ の外の根は Makefile の PACKAGE_EXTRA_TEST_ROOTS へ足す)、"
        "tests/test_daily_test_population.py の EXCLUDED に理由つきで載せる(呼び手の無い木の緑は日次に見えない)"
        "— ADR-DOE-ENFORCE-001 R8"
    )


def _assert_every_test_named_source_is_collected(repo: Path) -> None:
    """検のつもりの名(test_*.py・test_*.hy)の file は、どれかの集め手の pattern に当たる(fixtures の下を除く)。

    当たらない file は、母集団の根の下に在っても pytest に集められず、日次で 1 本も走らない(agora-redesign #2591 —
    packages/doeff-hy/tests の 6 本は、集める conftest の無い dir の test_*.hy だった)。
    """
    patterns = _test_file_patterns(repo)
    uncollected = sorted(
        rel
        for current, _dirs, files in _walk(repo)
        for rel in (Path(current, name).relative_to(repo).as_posix() for name in files)
        if _is_test_file(rel, _TEST_NAMED_SOURCES)
        and not _is_test_file(rel, patterns)
        and "fixtures" not in rel.split("/")
    )
    assert not uncollected, (
        f"どの集め手にも集められない検の file が {len(uncollected)} 本: {uncollected} — 集め手の pattern"
        "(root の ini の python_files・doeff_hy_test_files・doeff-adr の executable ADR の pattern)に当たる名にするか、"
        "集め手の pattern を ini に足す(package ごとの conftest.py に集め手を写さない)— ADR-DOE-ENFORCE-001 R8"
    )


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
    # 子(make)は doeff の子 process の答え手が起こし、今の環境に偽の runner の 2 つの名を重ねて渡す(agora-redesign #3012)。
    proc = run(
        with_handlers(
            [subprocess_handler],
            RunProcess(
                argv=(
                    "make",
                    "-s",
                    "-C",
                    str(REPO_ROOT),
                    "test-packages",
                    f"PACKAGE_UV_RUN={sys.executable} {runner}",
                ),
                env=(
                    EnvEntry(name="DOEFF_FAKE_RUNNER_RECORD", value=str(record)),
                    EnvEntry(
                        name="DOEFF_FAKE_RUNNER_FAIL",
                        value=str(REPO_ROOT / "packages" / first / "tests"),
                    ),
                ),
                env_mode=EnvMode.EXTEND,
                timeout=120.0,
            ),
        )
    )
    output = proc.stdout + proc.stderr
    assert not proc.timed_out, f"make test-packages が 120 秒で終わらなかった\n{output[-2000:]}"
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
    extra_roots = _package_extra_test_roots(REPO_ROOT)
    passed = {Path(call["cwd"], arg).resolve() for call in calls for arg in call["argv"]}
    missed_roots = [root for root in extra_roots if (REPO_ROOT / root).resolve() not in passed]
    assert not missed_roots, (
        f"make test-packages が package の tests/ の外の根 {missed_roots} を訪ねていない(最初の {first}"
        f" だけを赤にした)— その根の検は日次で 1 本も走らない — R8\n{output[-2000:]}"
    )
    assert proc.exit_code != 0, (
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

    # 模型の木は root の conftest も外の plugin も持たない — 外の走行の設定を持ち込まない。子(make)は doeff の子 process の
    # 答え手が起こし、今の環境にこの 3 つの名を重ねて渡す(agora-redesign #3012)。
    proc = run(
        with_handlers(
            [subprocess_handler],
            RunProcess(
                argv=(
                    "make",
                    "-s",
                    "-f",
                    str(REPO_ROOT / "Makefile"),
                    "-C",
                    str(tmp_path),
                    "test-packages",
                    f"PACKAGE_UV_RUN={sys.executable} -m",
                ),
                env=(
                    EnvEntry(name="PYTEST_DISABLE_PLUGIN_AUTOLOAD", value="1"),
                    EnvEntry(name="PYTEST_ADDOPTS", value="-p no:cacheprovider"),
                    EnvEntry(name="PYTHONDONTWRITEBYTECODE", value="1"),
                ),
                env_mode=EnvMode.EXTEND,
                timeout=120.0,
            ),
        )
    )
    output = proc.stdout + proc.stderr
    assert not proc.timed_out, f"make test-packages が 120 秒で終わらなかった\n{output}"
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
    assert proc.exit_code != 0, f"失敗の package があるのに rc が 0 — R8\n{output}"
    named = _named_failed_packages(output)
    assert named == ["a", "b"], (
        f"最後に失敗の package (a b) を `test-packages failed:` の行で名指していない: {named}"
        f" — R8\n{output}"
    )


def test_every_test_file_belongs_to_a_daily_population() -> None:
    """検の file(.py・.hy)は日次の母集団の根(root の testpaths・package の tests・Makefile の
    PACKAGE_EXTRA_TEST_ROOTS)の下か、除外の表に在る。"""
    _assert_every_test_file_belongs_to_a_daily_population(REPO_ROOT, _population_roots(REPO_ROOT))


def test_hy_test_outside_every_population_is_red_until_its_root_is_declared(
    tmp_path: Path,
) -> None:
    """失敗ケース: package の tests/ の外に `.hy` の検だけを持つ dir を置くと赤、その dir を本物の
    Makefile の PACKAGE_EXTRA_TEST_ROOTS に入れると緑(#2542 の sim の dir の形・agora-redesign #2577)。

    `.py` だけを数えていた時の規則では、この `.hy` は検の file に数えられず、母集団の外でも緑だった。
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\ndoeff_hy_test_files = ["test_*.hy"]\n',
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    sim = "packages/demo/src/demo/sim"
    hy_test = tmp_path / sim / "test_on_sim.hy"
    hy_test.parent.mkdir(parents=True)
    hy_test.write_text("(deftest test-on-sim (assert True))\n", encoding="utf-8")

    assert f"{sim}/test_on_sim.hy" in _test_named_files(tmp_path), (
        "母集団の外の `.hy` の検が検の file に数えられていない — R8"
    )
    # Makefile の既定の根(doeff-cluster の sim)は模型の木に無い — この dir は母集団の外。
    with pytest.raises(AssertionError, match=re.escape(f"{sim}/test_on_sim.hy")):
        _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))
    declared = _population_roots(tmp_path, (f"PACKAGE_EXTRA_TEST_ROOTS={sim}",))
    assert sim in declared
    _assert_every_test_file_belongs_to_a_daily_population(tmp_path, declared)


def test_every_test_named_file_is_collected_by_some_collector() -> None:
    """test_*.py・test_*.hy の file はどれも、どれかの集め手の pattern に当たる(agora-redesign #2591)。"""
    _assert_every_test_named_source_is_collected(REPO_ROOT)


def test_hy_test_without_a_collector_is_red_until_the_collector_is_declared(tmp_path: Path) -> None:
    """失敗ケース: 母集団の根(root の testpaths)の下に、どの集め手にも集められない test_*.hy を 1 本置くと赤、
    root の ini の doeff_hy_test_files で集め手に当てると緑(agora-redesign #2591)。

    検の側に Hy の条件を `test_*.hy` と写していた #2577 の形では、ini に集め手が無くてもこの file は「検の file」と
    数えられ、母集団の根の下なので緑だった — packages/doeff-hy/tests の 6 本と同じ形。
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    script = tmp_path / "tests" / "test_script.hy"
    script.write_text("(assert (= (+ 1 1) 2))\n", encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")

    # 母集団の根の下に在る — 置き場の検は緑のまま(集められるかは別の事柄)。
    _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))
    with pytest.raises(AssertionError, match=re.escape("tests/test_script.hy")):
        _assert_every_test_named_source_is_collected(tmp_path)

    pyproject.write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\ndoeff_hy_test_files = ["test_*.hy"]\n',
        encoding="utf-8",
    )
    _assert_every_test_named_source_is_collected(tmp_path)


def test_hy_test_skips_name_hy_test_files_with_a_reason() -> None:
    """外す一覧(root の ini の doeff_hy_test_skips)の各行は、今の木の Hy の検の file を名指し、理由を持つ。

    一覧の読み方は集め手(doeff-adr の plugin)と同じ関数 — 理由の無い行はそこで止まる。直した file の行が
    残ると、その file は緑なのに日次で走らないまま skip に数えられる。
    """
    rows = parse_hy_test_skips(_root_ini(REPO_ROOT).get("doeff_hy_test_skips", ()))
    patterns = tuple(_root_ini(REPO_ROOT).get("doeff_hy_test_files", ()))
    stale = [
        row.path
        for row in rows.rows
        if not (REPO_ROOT / row.path).is_file() or not _is_test_file(row.path, patterns)
    ]
    assert not stale, (
        f"doeff_hy_test_skips に Hy の検の file でない行: {stale} — 消すか path を直す — ADR-DOE-ENFORCE-001 R8"
    )


def test_no_conftest_copies_the_hy_test_collector() -> None:
    """Hy の検の file の集め手は root の ini の doeff_hy_test_files の 1 点 — conftest.py に写さない。

    写しが残ると、その dir の外の test_*.hy は集められないまま母集団の検に数えられる(agora-redesign #2591 の前は
    7 つの conftest.py が同じ条件を書き、conftest の無い packages/doeff-hy/tests の 6 本が日次で走らなかった)。
    """
    copies = sorted(
        Path(current, "conftest.py").relative_to(REPO_ROOT).as_posix()
        for current, _dirs, files in _walk(REPO_ROOT)
        if "conftest.py" in files
        and "DoeffAdrHyFile" in Path(current, "conftest.py").read_text(encoding="utf-8")
    )
    assert not copies, (
        f"Hy の検の file を自前で集める conftest.py: {copies} — 集める条件は root の ini の doeff_hy_test_files へ"
        " — ADR-DOE-ENFORCE-001 R8"
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


def test_every_daily_stage_declares_whether_it_runs_pytest() -> None:
    """日次の全体検証は欄 pytest = true の処理ステージで session が 1 本も答えなければカバレッジの欠けに数え、欄の無い処理ステージは
    「pytest の実行は未宣言」と記帳する(agora-redesign #3879)。走らせ器は断らないので、欄の書き忘れはこの repo の宣言の検で赤にする。"""
    full = tomllib.loads((REPO_ROOT / ".agents" / "land-queue.toml").read_text(encoding="utf-8"))["gate"]["full"]
    assert isinstance(full, list) and full, f"[gate] full が処理ステージの列でない(1 本の文字列には欄 pytest を書けない): {full!r}"
    missing = [stage.get("name") for stage in full if not isinstance(stage.get("pytest"), bool)]
    assert not missing, f"[gate] full の処理ステージ {missing} に欄 pytest(true か false)が無い"
