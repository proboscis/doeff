"""木の .pyc を、保存先(:mod:`doeff_hy_bytecode_guard.code_store`)の中身の同じ file への hardlink で置く入口 :func:`link_pyc` の検
(card ki-d51b7baa38bc)。

実例(2026-10-17・Mac の worker): 実行環境の root を作り直すたびに、焼く道具が保存先の code から同じ中身の .pyc を root の木へ新しく
書いた(root 1 本 約 100 MB)。root を消した後も macOS の local snapshot がその中身を 24 時間持ち、作業 disk の空きが root の作り直しの
数だけ減った。直した後は、同じ中身の .pyc は保存先の 1 つの file を分け合い、root には新しい block を持たない。
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from doeff_hy_bytecode_guard import code_store

PYC = b"\x00" * 16 + b"marshalled code"


def test_two_trees_share_the_stores_one_file_for_the_same_pyc(tmp_path: Path) -> None:
    """同じ中身の .pyc を 2 つの木へ置くと、どちらも保存先の 1 つの file(名 = 中身の sha256)と同じ実体になる。"""
    store = str(tmp_path / "store")
    first, second = tmp_path / "a" / "__pycache__" / "m.pyc", tmp_path / "b" / "__pycache__" / "m.pyc"
    assert code_store.link_pyc(store, PYC, str(first)) is None
    assert code_store.link_pyc(store, PYC, str(second)) is None
    blob = Path(code_store.entry_path(store, hashlib.sha256(PYC).hexdigest(), code_store.PYC_SUFFIX))
    assert first.read_bytes() == second.read_bytes() == PYC
    assert first.stat().st_ino == second.stat().st_ino == blob.stat().st_ino
    assert blob.stat().st_nlink == 3


def test_the_stores_file_is_read_only_and_a_replaced_tree_pyc_leaves_the_others(tmp_path: Path) -> None:
    """保存先の file は読み取り専用(その場で開いて書く手を断る)。木の .pyc を別の中身で置き直しても(置き換え)、他の木の .pyc と保存先の
    中身は前のまま。"""
    store = str(tmp_path / "store")
    first, second = tmp_path / "a" / "m.pyc", tmp_path / "b" / "m.pyc"
    code_store.link_pyc(store, PYC, str(first))
    code_store.link_pyc(store, PYC, str(second))
    assert stat.S_IMODE(first.stat().st_mode) & 0o222 == 0
    other = PYC + b" changed"
    assert code_store.link_pyc(store, other, str(second)) is None
    assert second.read_bytes() == other
    assert first.read_bytes() == PYC
    assert first.stat().st_ino != second.stat().st_ino


def test_using_a_stored_pyc_moves_its_time(tmp_path: Path) -> None:
    """保存先の .pyc を使うたびに時刻を今にする(掃除が 7 日使われない物を消す — .code の entry と同じ使った印)。"""
    store = str(tmp_path / "store")
    code_store.link_pyc(store, PYC, str(tmp_path / "a" / "m.pyc"))
    blob = code_store.entry_path(store, hashlib.sha256(PYC).hexdigest(), code_store.PYC_SUFFIX)
    old = 1_000_000_000
    os.utime(blob, (old, old))
    code_store.link_pyc(store, PYC, str(tmp_path / "b" / "m.pyc"))
    assert os.stat(blob).st_mtime > old


def test_placing_the_same_pyc_again_leaves_no_temporary_link(tmp_path: Path) -> None:
    """木の .pyc が既に保存先の同じ file の名なら置き直さない(同じ実体への rename は何もせず、一時の link が残って名が 1 つ増える —
    査読の懸念)。"""
    store = str(tmp_path / "store")
    target = tmp_path / "a" / "m.pyc"
    assert code_store.link_pyc(store, PYC, str(target)) is None
    assert code_store.link_pyc(store, PYC, str(target)) is None
    assert sorted(p.name for p in target.parent.iterdir()) == ["m.pyc"]
    assert target.stat().st_nlink == 2


def test_a_stored_pyc_left_writable_is_made_read_only_when_used(tmp_path: Path) -> None:
    """読み取り専用にする前に書き手が落ちて書ける mode のまま残った保存先の file も、使う時に読み取り専用に揃える。"""
    store = str(tmp_path / "store")
    code_store.link_pyc(store, PYC, str(tmp_path / "a" / "m.pyc"))
    blob = code_store.entry_path(store, hashlib.sha256(PYC).hexdigest(), code_store.PYC_SUFFIX)
    os.chmod(blob, 0o600)
    assert code_store.link_pyc(store, PYC, str(tmp_path / "b" / "m.pyc")) is None
    assert stat.S_IMODE(os.stat(blob).st_mode) == 0o444


def test_a_second_writer_shares_the_bake_tools_file_through_the_entry_api(tmp_path: Path) -> None:
    """同じ entry から .pyc を置く 2 つの書き手(焼く道具の形 = entry_pyc の bytes を link_pyc・.pyc を寄せる別の書き手 = link_entry_pyc)は、
    組み方を自分で持たずに保存先の 1 つの file を分け合う(設計検証 lt-4KTRWD3K1PH2VG5W5X294EZJP4 の盲検 A — 組み方が焼く道具にだけ在ると、
    2 つ目の書き手は marshal し直して root ごとに別の file を作った)。"""
    store = str(tmp_path / "store")
    key = code_store.code_key("/tree/pkg/m.py", b"VALUE = 1\n", "pkg.m", "1.1.0", "cpython-314", 0)
    assert code_store.add_code(store, key, compile("VALUE = 1\n", "/tree-a/pkg/m.py", "exec")) is None
    entry = code_store.current_entry(store, key, lambda code: True)
    assert entry is not None
    head = b"\x00" * 16
    first, second = tmp_path / "a" / "m.pyc", tmp_path / "b" / "m.pyc"
    assert code_store.link_pyc(store, code_store.entry_pyc(head, entry), str(first)) is None
    again = code_store.current_entry(store, key, lambda code: True)
    assert again is not None and code_store.link_entry_pyc(store, head, again, str(second)) is None
    assert first.stat().st_ino == second.stat().st_ino
    assert second.read_bytes() == head + entry.data


def test_a_target_that_cannot_be_linked_answers_the_reason(tmp_path: Path) -> None:
    """木へ hardlink を置けない時(ここでは木の dir の場所に file が在る)は例外にせず、path を含まない理由を答える(呼び手は中身を書く —
    理由は準備の記録に 1 度ずつ出る)。"""
    store = str(tmp_path / "store")
    blocker = tmp_path / "tree"
    blocker.write_text("not a dir")
    problem = code_store.link_pyc(store, PYC, str(blocker / "__pycache__" / "m.pyc"))
    assert problem is not None and "hardlink で置けない" in problem, problem
    assert str(tmp_path) not in problem, problem
