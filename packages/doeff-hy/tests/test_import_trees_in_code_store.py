"""import の compile が展開した木は、bytecode の置き場(code_store — 環境変数 DOEFF_HY_CODE_STORE)の下に残るの検。

実例(2026-10-19・日次): 木は DOEFF_EFFECT_ANALYZER_CACHE が dir を指す process の compile だけが置いていた。bytecode の置き場は worker の
上の job が全部 DOEFF_HY_CODE_STORE で共有するのに、analyzer の置き場の変数は worker が job の子へ渡す許可表に無い。変わった module を
先にマージの列の検証の job が compile すると、bytecode の置き場は温まって木は残らず、日次は bytecode に当たって compile せず、閉じの検の
analyzer が同じ source を展開し直した(日次の 2 つの閉じの検の和 557.5 秒・置き場を分けた再現は 151.9 秒)。

木の置き場は bytecode の置き場の中の 1 つ — 木を置く process と引く analyzer は、同じ名指しの置き場だけを見る。名指しの無い既定の dir
(利用者の cache)には置かない(普通の import の compile に木の書きを足さない)。木は bytecode の数倍の大きさなので、圧縮して置き、鍵 1 つの
entry の数に上限を持つ(7 日使われない entry は worker の掃除が消す — 引いて当たるたびに時刻を今にする)。
"""

from __future__ import annotations

import ast
import os
import pickle
import time
from pathlib import Path

import pytest
from doeff_hy_bytecode_guard import _code_store as code_store
from doeff_hy_bytecode_guard import _expansion_trees as expansion_trees
from doeff_hy_bytecode_guard._records import MacroRecord

SOURCE = "(setv answer 42)\n"
TREE = ast.parse("answer = 42\n")


def record(label: str = "") -> MacroRecord:
    """展開が使った macro の記録の代役(macro 無し — ``label`` が違えば記録の指紋が違う = macro の環境の違う書き手)。"""
    return MacroRecord(hy_version=f"probe{label}", macros=(), absent=(), providers=(), values=(), files=())


@pytest.fixture(autouse=True)
def records_are_current(monkeypatch: pytest.MonkeyPatch) -> None:
    """照らしは今の環境の macro の file を読むので、ここでは記録を全部「今の環境に合う」とする(合わない時の検は別に持つ)。"""
    monkeypatch.setattr(expansion_trees, "record_is_current_here", lambda _macros: True)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """名指しの bytecode の置き場(analyzer の置き場の変数は無い — 検証の job の形)。"""
    directory = tmp_path / "code-store"
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(directory))
    monkeypatch.delenv("DOEFF_EFFECT_ANALYZER_CACHE", raising=False)
    return directory


def trees_under(root: Path) -> list[Path]:
    """root の下の木の entry。"""
    return sorted(root.rglob(f"*{code_store.TREE_SUFFIX}"))


def test_a_tree_is_left_in_the_named_code_store_and_read_from_there(store: Path) -> None:
    """analyzer の置き場の変数の無い process が置いた木を、同じ置き場を名指す process が引ける。"""
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), TREE)
    assert len(trees_under(store)) == 1
    found = expansion_trees.read_import_tree("pkg.m", SOURCE)
    assert found is not None
    assert ast.dump(found.tree) == ast.dump(TREE)
    assert found.macros == record()


@pytest.mark.parametrize("name", ["module", "source"])
def test_another_module_or_source_does_not_find_the_tree(store: Path, name: str) -> None:
    """鍵は module の名と source の中身 — どちらが違っても当たらない。"""
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), TREE)
    other = ("pkg.n", SOURCE) if name == "module" else ("pkg.m", SOURCE + ";; changed\n")
    assert expansion_trees.read_import_tree(*other) is None


@pytest.mark.parametrize("setting", [None, "", "off"])
def test_a_store_that_is_not_named_gets_no_tree(
    setting: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """名指しの無い(未設定・空・off)置き場には置かず、引かない — 既定の dir(利用者の cache)にも書かない。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DOEFF_EFFECT_ANALYZER_CACHE", raising=False)
    if setting is None:
        monkeypatch.delenv("DOEFF_HY_CODE_STORE", raising=False)
    else:
        monkeypatch.setenv("DOEFF_HY_CODE_STORE", setting)
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), TREE)
    assert trees_under(tmp_path) == []
    assert expansion_trees.read_import_tree("pkg.m", SOURCE) is None


def test_the_analyzers_variable_does_not_name_a_place_for_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """木の置き場は 1 つ(bytecode の置き場の中)— analyzer の置き場の変数だけでは置かない(2 つ目の定義元を作らない)。"""
    monkeypatch.delenv("DOEFF_HY_CODE_STORE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", str(tmp_path / "analyzer"))
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), TREE)
    assert trees_under(tmp_path) == []


def test_a_tree_that_is_not_a_module_is_not_kept(store: Path) -> None:
    """展開の木が module でない物(型検査の展開の code など)は置かない。"""
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), object())
    assert trees_under(store) == []


def test_a_tree_is_kept_compressed(store: Path) -> None:
    """木は bytecode の数倍の大きさなので圧縮して置く — 置いた entry が、木の pickle そのままより小さい。"""
    big = ast.parse("".join(f"value_{index} = [{index}, 'text_{index}', ({index}, {index + 1})]\n" for index in range(2000)))
    expansion_trees.add_import_tree("pkg.big", SOURCE, record(), big)
    [entry] = trees_under(store)
    assert entry.stat().st_size * 3 < len(pickle.dumps(big, protocol=pickle.HIGHEST_PROTOCOL))
    found = expansion_trees.read_import_tree("pkg.big", SOURCE)
    assert found is not None
    assert ast.dump(found.tree) == ast.dump(big)


def test_a_tree_of_other_macros_is_not_read(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """引くのは、展開が依った macro が今の環境の物の entry だけ — 古い macro の木は使わない。"""
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), TREE)
    monkeypatch.setattr(expansion_trees, "record_is_current_here", lambda _macros: False)
    assert expansion_trees.read_import_tree("pkg.m", SOURCE) is None


def test_the_entry_of_the_current_macros_is_the_one_read(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """macro の環境の違う 2 つの書き手の木が並んでも、今の環境に合う方を引く。"""
    old, new = ast.parse("answer = 1\n"), ast.parse("answer = 2\n")
    expansion_trees.add_import_tree("pkg.m", SOURCE, record("-old"), old)
    expansion_trees.add_import_tree("pkg.m", SOURCE, record("-new"), new)
    monkeypatch.setattr(expansion_trees, "record_is_current_here", lambda macros: macros.hy_version == "probe-new")
    found = expansion_trees.read_import_tree("pkg.m", SOURCE)
    assert found is not None
    assert ast.dump(found.tree) == ast.dump(new)


def test_a_damaged_entry_is_skipped(store: Path) -> None:
    """壊れた entry は使わず、読む側は落ちない(analyzer が自分で展開する)。"""
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), TREE)
    [entry] = trees_under(store)
    entry.write_bytes(entry.read_bytes()[:-5])
    assert expansion_trees.read_import_tree("pkg.m", SOURCE) is None
    entry.write_bytes(b"not a pickle")
    assert expansion_trees.read_import_tree("pkg.m", SOURCE) is None


def test_reading_a_tree_marks_it_used(store: Path) -> None:
    """引いて当たるたびに entry の時刻を今にする — worker の掃除(7 日使われない entry を消す)が、使っている木を消さない。"""
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), TREE)
    [entry] = trees_under(store)
    week_ago = time.time() - 6 * 24 * 3600
    os.utime(entry, (week_ago, week_ago))
    assert expansion_trees.read_import_tree("pkg.m", SOURCE) is not None
    assert time.time() - entry.stat().st_mtime < 60


def test_a_key_keeps_at_most_the_limit_of_environments_and_the_used_ones_stay(store: Path) -> None:
    """鍵 1 つの木の entry は上限まで — 越える環境の分は、足しただけで使われていない entry から除く(使い続ける環境の entry を押し出さない)。"""
    limit = code_store.TREE_ENTRIES_PER_KEY
    for index in range(limit):
        expansion_trees.add_import_tree("pkg.m", SOURCE, record(f"-{index}"), TREE)
    entries = trees_under(store)
    assert len(entries) == limit
    used = entries[0]
    day_ago = time.time() - 3600
    for entry in entries:
        os.utime(entry, (day_ago, day_ago))
    os.utime(used, None)  # 使い続けている環境の entry
    for index in range(limit, limit + 3):
        expansion_trees.add_import_tree("pkg.m", SOURCE, record(f"-{index}"), TREE)
    after = trees_under(store)
    assert len(after) == limit
    assert used in after
