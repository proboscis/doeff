"""契約の検の表(root の pyproject.toml の `[[tool.doeff.contract-tests]]`)が package の増減とずれたら赤にする検
(agora-redesign #2658 — #2605 の残る穴の 4「契約の表は手で保つ」)。

表は手で保つ。package(uv の workspace の member — `packages/<名>/`)を足しても表に組が無ければ、その package の stub の
突き合わせなどは登記の前の入口(`make test-changed`)で選ばれないまま黙る。この file は:

1. 本物の木で、どの package も表に契約の組か理由つきの除外(`reason` の行)を持ち、表に書いた検の file が全部在る
   (読み手 read_contract_table は書いた検の file が無いと止める — その止まりを日次で確かめる)。
2. 失敗ケース — 本物の pyproject.toml の写しを持つ模型の repo で: package を 1 つ足して表を直さないと名指す・表の検の file を
   消すと読み手が止まる・組の在る package を消すと読み手が止まる・理由の無い除外(reason の鍵が無い・空・空白だけ)と
   package でない接頭辞の除外は読み手が止まる。

日次の母集団(tests/)と、表の repo 全体の行(prefix = "")の両方から走る — 変えた所の検は、どの変更でもこの file を先頭の
組で走らせる。入口の main は呼ばず、読み手の純粋な定義(read_contract_table・workspace_packages・table_gaps)を直に呼ぶ。
"""

from __future__ import annotations

import importlib.util
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
