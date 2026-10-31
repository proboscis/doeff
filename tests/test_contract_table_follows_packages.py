"""契約の検の表(root の pyproject.toml の `[[tool.doeff.contract-tests]]`)が package の増減とずれたら赤にする検
(#2605 の残る穴の 4「契約の表は手で保つ」)。

表は手で保つ。package(uv の workspace の member — `packages/<名>/`)を足しても表に組が無ければ、その package の stub の
突き合わせなどは登記の前の入口(`make test-changed`)で選ばれないまま黙る。この file は:

1. 本物の木で、どの package も表に契約の組か理由つきの除外(`reason` の行)を持ち、表に書いた検の file が全部在る
   (読み手 read_contract_table は書いた検の file が無いと止める — その止まりを日次で確かめる)。
2. 失敗ケース — 本物の pyproject.toml の写しを持つ模型の repo で: package を 1 つ足して表を直さないと名指す・表の検の file を
   消すと読み手が止まる・組の在る package を消すと読み手が止まる・理由の無い除外(reason の鍵が無い・空・空白だけ)と
   package でない接頭辞の除外は読み手が止まる。
3. 木を歩く検が、歩く先の変更で選ばれる: doeff-traverse の例の検は examples/ を glob して子の process で走らせ、例も
   macro も import しないので、逆依存では選ばれない。例・期待の出力・例が require する doeff-hy の macro の module・
   macro の実行時の半分(`<-`・`!` の束ねの開き方を持つ doeff_core_effects._outcomes)を変えた差分は、本物の表から例の検を
   選ぶ(表に無かった間、例を壊す変更は登記の前に測られず、翌日の日次で初めて赤になった。
   実行時の半分は設計検証の盲検 B が見つけた — doeff-hy の行だけでは選ばれなかった)。
4. uv.lock の Hy の版で測る検が、uv.lock の変更と、uv.lock の版の読み手(scripts/semgrep_locked.py の locked_version)の
   変更で選ばれる: 版は data の file から読むので、逆依存では選ばれない。測る検は source から数える(
   test_bytecode_guard.py は uv.lock から版を読むのに表に無く、Hy を上げる便で選ばれず日次でだけ走った)。
5. handle・defhandler の節の形を作る doeff-hy の module(handle.hy・clause_ops.hy・expansion_names.py)の変更で、doeff-effect-analyzer の
   検が選ばれる: analyzer は Hy の source を本物の macro で展開して読むが doeff-hy を import しないので、逆依存では選ばれない
   (4f284efa9 が節の局所名を変えた時、analyzer の検 12 本と agora-controllers の閉じの検が赤のまま main に入った)。

日次の母集団(tests/)と、表の repo 全体の行(prefix = "")の両方から走る — 変えた所の検は、どの変更でもこの file を先頭の
組で走らせる。入口の main は呼ばず、読み手の純粋な定義(read_contract_table・workspace_packages・table_gaps)を直に呼ぶ。
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "run_changed_tests.py"
#: 模型に足す package の名(本物の木に無い名)。
ADDED = "packages/doeff-added-without-row/"
#: 例の検と、それが歩く例の dir(例の `.hy` と expected/ の期待の出力)と、例が require する macro の module。
EXAMPLES_CHECK = "packages/doeff-traverse/tests/test_examples_run.py"
EXAMPLES_DIR = "packages/doeff-traverse/examples"
EXAMPLE_MACROS = "packages/doeff-hy/src/doeff_hy/macros.hy"
#: macro の実行時の半分の module と、その file。doeff-hy の outcome_forms.hy は「実行時の意味は doeff_core_effects._outcomes が
#: 持ち、ここは形だけを作る」で、defk の本体の束ねは macros.hy が globals に置くこの module の open_bind を引く。
EXAMPLE_MACRO_RUNTIME_MODULE = "doeff_core_effects._outcomes"
EXAMPLE_MACRO_RUNTIME = "packages/doeff-core-effects/doeff_core_effects/_outcomes.py"
#: uv.lock と、doeff で uv.lock の版を読む 1 か所。
LOCK = "uv.lock"
LOCK_READER = "scripts/semgrep_locked.py"
#: handle・defhandler の節の形(節を回す関数の引数・節の操作が読む effect と続きの名)を作る doeff-hy の module(
#: 4f284efa9 が変えた 3 file — macro の定義元・節の操作・名の綴りの定義元)と、それを本物の macro で展開して読む analyzer の検の file。
HANDLER_EXPANSION_MODULES = (
    "packages/doeff-hy/src/doeff_hy/handle.hy",
    "packages/doeff-hy/src/doeff_hy/_clause_ops.hy",
    "packages/doeff-hy/src/doeff_hy/_expansion_names.py",
)
ANALYZER_EXPANSION_CHECKS = (
    "packages/doeff-effect-analyzer/tests/python/test_program_effects.py",
    "packages/doeff-effect-analyzer/tests/python/test_guarded_clauses.py",
)
#: analyzer の検が Hy の source の中で本物の defhandler を require する行(展開を通す印 — doeff-hy.macros か doeff-hy.handle から)。
HANDLER_MACRO_REQUIRE = re.compile(r"\(require doeff-hy\.(?:macros|handle) \[[^\]]*\bdefhandler\b")
#: 検の source が読み手を Hy の版の読みに呼ぶ行(Python の locked_version・Hy の .locked-version の呼びで、package に hy の名を
#: 渡す行)。
LOCKED_HY_READ = re.compile(r'locked[-_]version\b[^\n]*"hy"')
#: 測る検を数える時に降りない dir(venv・git・build の出力 — 検の source を持たない)。
NOT_WALKED = re.compile(r"^(\..*|node_modules|target|build|dist|__pycache__)$")


@dataclass(frozen=True)
class Copy:
    """本物の表の写しを持つ模型の repo と、写した時の表(組の行の接頭辞と検の path)。"""

    root: Path
    contract_prefixes: tuple[str, ...]
    test_paths: tuple[str, ...]


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


def _touch(path: Path) -> None:
    """模型の木に空の file を置く(読み手が見るのは在るか無いかだけ)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _copy_of_real_table(runner: ModuleType, tmp_path: Path) -> Copy:
    """本物の pyproject.toml をそのまま写し、表と workspace が名指す木の形(package の pyproject.toml・組の接頭辞の dir・
    検の file)だけを空の file で置いた模型の repo を作る — 表を書き換えずに、木の側だけを動かして確かめるため。"""
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copy2(REPO_ROOT / "pyproject.toml", root / "pyproject.toml")
    units = runner.workspace_packages(REPO_ROOT)
    table = runner.read_contract_table(REPO_ROOT)
    test_paths = tuple(sorted({test.split("::", 1)[0] for row in table.rows for test in row.tests}))
    for prefix in units.prefixes:
        _touch(root / prefix / "pyproject.toml")
    # package の中の dir の行(例 doeff-hy の src)— 読み手は接頭辞の dir(`/` で終わる)か file が木に在る事を求める。
    for row in table.rows:
        if row.prefix.endswith("/"):
            (root / row.prefix).mkdir(parents=True, exist_ok=True)
        elif row.prefix:
            _touch(root / row.prefix)
    for path in test_paths:
        _touch(root / path)
    return Copy(
        root=root,
        contract_prefixes=tuple(row.prefix for row in table.rows),
        test_paths=test_paths,
    )


def _append_row(root: Path, row: str) -> None:
    """模型の pyproject.toml の終わりに表の行を 1 つ足す(`[[tool.doeff.contract-tests]]` の後の鍵の行)。"""
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(f"{text}\n[[tool.doeff.contract-tests]]\n{row}\n", encoding="utf-8")


def test_every_package_has_a_contract_row_or_a_reasoned_exemption(runner: ModuleType) -> None:
    """本物の木: 表は読め(書いた検の file は全部在る)、どの package にも契約の組か理由つきの除外が在る。"""
    table = runner.read_contract_table(REPO_ROOT)
    units = runner.workspace_packages(REPO_ROOT)
    assert units.prefixes, "uv の workspace の member が 0 個 — 単位の読みが壊れている"
    gaps = runner.table_gaps(table, units)
    assert gaps.uncovered == (), (
        "契約の組も理由つきの除外も無い package: "
        f"{list(gaps.uncovered)} — root の pyproject.toml の [[tool.doeff.contract-tests]] に、その package の公開の形を"
        " 全体で確かめる検(stub の突き合わせ・公開の名・母集団)の組を足すか、prefix と reason の行で要らない理由を書く"
    )
    assert all(row.reason for row in table.exemptions), table.exemptions


def test_changing_an_example_or_its_macros_selects_the_examples_check(runner: ModuleType) -> None:
    """本物の表: 例 1 つ・期待の出力 1 つ・例が require する doeff-hy の macro の module・macro の実行時の半分の module の
    どれを変えた差分も、変えた所の検の選び(入口と同じ select_targets)が例の検を選ぶ。"""
    table = runner.read_contract_table(REPO_ROOT)
    patterns = runner.collector_patterns(REPO_ROOT)
    examples = REPO_ROOT / EXAMPLES_DIR
    changed = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in (*examples.glob("*.hy"), *(examples / "expected").glob("*.txt"))
    )
    assert any(p.endswith(".hy") for p in changed), f"{EXAMPLES_DIR} に例が無い — 検の前提が崩れた"
    assert any(p.endswith(".txt") for p in changed), f"{EXAMPLES_DIR}/expected に期待の出力が無い"
    assert (REPO_ROOT / EXAMPLE_MACROS).is_file(), (
        f"{EXAMPLE_MACROS} が無い — 例が require する module を直す"
    )
    # macro が実行時の半分を別の module へ移したら、ここの module の名と file を直す(古い名のまま緑にしない)。
    assert f'"{EXAMPLE_MACRO_RUNTIME_MODULE}"' in (REPO_ROOT / EXAMPLE_MACROS).read_text(
        encoding="utf-8"
    ), (
        f"{EXAMPLE_MACROS} が実行時の module {EXAMPLE_MACRO_RUNTIME_MODULE} を名指さない — 移した先の module を書く"
    )
    assert EXAMPLE_MACRO_RUNTIME.endswith(EXAMPLE_MACRO_RUNTIME_MODULE.replace(".", "/") + ".py"), (
        EXAMPLE_MACRO_RUNTIME
    )
    assert (REPO_ROOT / EXAMPLE_MACRO_RUNTIME).is_file(), f"{EXAMPLE_MACRO_RUNTIME} が無い"
    unselected = [
        path
        for path in (*changed, EXAMPLE_MACROS, EXAMPLE_MACRO_RUNTIME)
        if EXAMPLES_CHECK
        not in {
            target.arg
            for target in runner.select_targets(
                table, runner.ChangeSet(base="", files=(path,)), patterns, REPO_ROOT
            ).targets
        }
    ]
    assert unselected == [], (
        f"例の検 {EXAMPLES_CHECK} を選ばない変更: {unselected} — root の pyproject.toml の [[tool.doeff.contract-tests]]"
        " で、その path に当たる行の tests に例の検を足す(例の検は例も macro も import しないので、逆依存では選ばれない)"
    )


def _walked_files() -> Iterator[Path]:
    """repo の file(NOT_WALKED の dir へ降りない)。"""
    for current, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = sorted(name for name in dirs if not NOT_WALKED.match(name))
        yield from (Path(current, name) for name in files)


def _locked_hy_checks(runner: ModuleType) -> tuple[str, ...]:
    """uv.lock の Hy の版で測る検の file — 集め手の名の規則に当たる file のうち、source が読み手を Hy の版の読みに呼ぶ物。"""
    patterns = runner.collector_patterns(REPO_ROOT)
    return tuple(
        sorted(
            rel
            for path in _walked_files()
            for rel in (path.relative_to(REPO_ROOT).as_posix(),)
            if runner.is_test_file(rel, patterns)
            and LOCKED_HY_READ.search(path.read_text(encoding="utf-8"))
        )
    )


def _selected_files(runner: ModuleType, changed: str) -> frozenset[str]:
    """file changed 1 つを変えた差分で、変えた所の検の選び(入口と同じ select_targets)が選ぶ検の file。"""
    selection = runner.select_targets(
        runner.read_contract_table(REPO_ROOT),
        runner.ChangeSet(base="", files=(changed,)),
        runner.collector_patterns(REPO_ROOT),
        REPO_ROOT,
    )
    return frozenset(target.arg.split("::", 1)[0] for target in selection.targets)


def test_changing_the_lock_or_its_reader_selects_every_check_measuring_the_locked_hy(
    runner: ModuleType,
) -> None:
    """本物の表: uv.lock を変えた差分も、uv.lock の版の読み手を変えた差分も、uv.lock の Hy の版で測る検の file を全部選ぶ
    (file の一部の検を選べば足りる)。測る検は source から数える — 写しの一覧を置かない。"""
    checks = _locked_hy_checks(runner)
    assert checks, (
        f"{LOCK_READER} を Hy の版の読みに呼ぶ検が 0 本 — 読み手の名か呼び方が変わったら LOCKED_HY_READ を直す"
    )
    selected = {changed: _selected_files(runner, changed) for changed in (LOCK, LOCK_READER)}
    unselected = [
        (changed, check)
        for changed, files in selected.items()
        for check in checks
        if check not in files
    ]
    assert unselected == [], (
        f"uv.lock の Hy の版で測る検を選ばない変更 #(変えた file 検): {unselected} — root の pyproject.toml の"
        " [[tool.doeff.contract-tests]] で、その path に当たる行の tests に検を足す(版は data の file から読むので、"
        "逆依存では選ばれない)"
    )


def test_changing_the_handler_expansion_selects_the_analyzer_checks(runner: ModuleType) -> None:
    """本物の表: handle・defhandler の節の形を作る doeff-hy の module(4f284efa9 が変えた 3 file)のどれを変えた差分も、
    変えた所の検の選び(入口と同じ select_targets)が doeff-effect-analyzer の検の file を選ぶ。analyzer の検は Hy の source に
    `(require doeff-hy.macros [defhandler …])` を書いて本物の macro で展開してから読み、doeff-hy を import しないので、逆依存では選ばれない
    (4f284efa9 が節の局所名を変えた時、analyzer の検 12 本と agora-controllers の閉じの検が赤のまま main に
    入り、日次で初めて出た)。行を外すと、この検が赤になる。"""
    for module in HANDLER_EXPANSION_MODULES:
        assert (REPO_ROOT / module).is_file(), f"{module} が無い — 移した先・改めた名を書く"
    for check in ANALYZER_EXPANSION_CHECKS:
        source = (REPO_ROOT / check).read_text(encoding="utf-8")
        # 検が本物の macro を通さなくなったら、この選びは何も守らない(古い前提のまま緑にしない)。
        assert HANDLER_MACRO_REQUIRE.search(source), (
            f"{check} が本物の defhandler を require しない — 展開した本物の節を読む検でなくなった。選ぶ検を直す"
        )
    unselected = [
        (module, check)
        for module in HANDLER_EXPANSION_MODULES
        for check in ANALYZER_EXPANSION_CHECKS
        if check not in _selected_files(runner, module)
    ]
    assert unselected == [], (
        f"analyzer の検を選ばない変更 #(変えた file 検): {unselected} — root の pyproject.toml の [[tool.doeff.contract-tests]] で、"
        "doeff-hy の src の行の tests に analyzer の検を足す(analyzer は doeff-hy を import しないので、逆依存では選ばれない)"
    )


@dataclass(frozen=True)
class Uncovered:
    """木 1 つの、組も理由つきの除外も無い package の接頭辞(名の順)。"""

    prefixes: tuple[str, ...]


def _gaps(runner: ModuleType, root: Path) -> Uncovered:
    """木 root の表を読み、組も理由つきの除外も無い package を返す — 模型の前後を比べるため(読めなければ読み手が止まる)。"""
    gaps = runner.table_gaps(runner.read_contract_table(root), runner.workspace_packages(root))
    return Uncovered(prefixes=gaps.uncovered)


def test_the_copy_of_the_real_table_reads_like_the_real_tree(
    runner: ModuleType, tmp_path: Path
) -> None:
    """模型が本物の表を写せている確かめ(下の失敗ケースの土台): 写しは読め、組の行とずれは本物の木と同じ。
    下の失敗ケースは写しの前後の差だけを見る — 本物の木のずれは上の検だけが赤にする(同じ赤を 2 度数えない)。"""
    copy = _copy_of_real_table(runner, tmp_path)
    table = runner.read_contract_table(copy.root)
    assert tuple(row.prefix for row in table.rows) == copy.contract_prefixes
    assert _gaps(runner, copy.root) == _gaps(runner, REPO_ROOT)


def test_a_package_added_without_a_row_is_named(runner: ModuleType, tmp_path: Path) -> None:
    """失敗ケース: package を 1 つ足して表を直さないと、その package を名指す(赤)。pyproject.toml の無い dir
    (退役した crate の追跡外の残り)と workspace の exclude に当たる dir は package ではないので名指さない。"""
    copy = _copy_of_real_table(runner, tmp_path)
    before = _gaps(runner, copy.root)
    _touch(copy.root / ADDED / "pyproject.toml")
    _touch(copy.root / "packages" / "doeff-residue" / "Cargo.lock")
    _touch(copy.root / "packages" / "doeff-agentd" / "pyproject.toml")
    after = _gaps(runner, copy.root)
    assert ADDED not in before.prefixes, before
    assert after.prefixes == tuple(sorted({*before.prefixes, ADDED})), after


def test_a_reasoned_exemption_closes_the_gap(runner: ModuleType, tmp_path: Path) -> None:
    """足した package に理由つきの除外を書けば、ずれは閉じる(黙って外すのではなく、理由が表に残る)。"""
    copy = _copy_of_real_table(runner, tmp_path)
    before = _gaps(runner, copy.root)
    _touch(copy.root / ADDED / "pyproject.toml")
    _append_row(copy.root, f'prefix = "{ADDED}"\nreason = "公開の形を全体で確かめる検が無い(模型)"')
    assert _gaps(runner, copy.root) == before
    added = [row for row in runner.read_contract_table(copy.root).exemptions if row.prefix == ADDED]
    assert [row.reason for row in added] == ["公開の形を全体で確かめる検が無い(模型)"], added


def test_removing_a_test_file_named_by_the_table_stops_the_reader(
    runner: ModuleType, tmp_path: Path
) -> None:
    """失敗ケース: 表に書いた検の file を消すと、読み手が名指して止まる(入口は rc 2・この検は赤)。"""
    copy = _copy_of_real_table(runner, tmp_path)
    removed = copy.test_paths[0]
    (copy.root / removed).unlink()
    with pytest.raises(runner.StopError, match="書いた検の file が木に無い") as caught:
        runner.read_contract_table(copy.root)
    assert removed in str(caught.value)


def test_removing_a_package_with_a_row_stops_the_reader(runner: ModuleType, tmp_path: Path) -> None:
    """失敗ケース(減る向き): 組の在る package の dir を消して表を直さないと、読み手が接頭辞を名指して止まる。"""
    copy = _copy_of_real_table(runner, tmp_path)
    removed = next(p for p in copy.contract_prefixes if p.startswith("packages/"))
    shutil.rmtree(copy.root / removed)
    with pytest.raises(runner.StopError, match="が木に無い") as caught:
        runner.read_contract_table(copy.root)
    assert removed in str(caught.value)


@pytest.mark.parametrize(
    ("row", "named"),
    [
        (f'prefix = "{ADDED}"', "鍵は"),
        (f'prefix = "{ADDED}"\nreason = ""', "空でない理由"),
        (f'prefix = "{ADDED}"\nreason = "   "', "空でない理由"),
        (f'prefix = "{ADDED}"\nreason = 1', "空でない理由"),
        (
            'prefix = "packages/"\nreason = "package でない接頭辞"',
            "package(uv の workspace の member の dir)でない",
        ),
        (
            'prefix = ""\nreason = "repo 全体を外す"',
            "package(uv の workspace の member の dir)でない",
        ),
        (f'prefix = "{ADDED}"\nreason = "両方"\ntests = ["tests/test_x.py"]', "鍵は"),
    ],
    ids=[
        "reason-missing",
        "reason-empty",
        "reason-blank",
        "reason-not-text",
        "not-a-package",
        "repo-wide",
        "both",
    ],
)
def test_an_exemption_without_a_reason_stops_the_reader(
    runner: ModuleType, tmp_path: Path, row: str, named: str
) -> None:
    """失敗ケース: 理由の無い除外・package でない接頭辞の除外・組と除外を混ぜた行は、読み手が止まる(黙って外さない)。"""
    copy = _copy_of_real_table(runner, tmp_path)
    _touch(copy.root / ADDED / "pyproject.toml")
    _append_row(copy.root, row)
    with pytest.raises(runner.StopError, match=re.escape(named)):
        runner.read_contract_table(copy.root)
