"""Check on :func:`link_pyc`, the entry point that places a tree's .pyc as a hardlink to the file with the same content in the store (:mod:`doeff_hy_bytecode_guard._code_store`).

Real case (2026-10-17, a Mac worker): each time the execution-environment root was rebuilt, the baking tool wrote a fresh .pyc with the same content from the store's code into the root's tree
(about 100 MB per root). Even after the root was deleted, macOS's local snapshot held that content for 24 hours, and free work-disk space shrank by the number of
root rebuilds. After the fix, .pyc files with the same content share one file in the store, and the root holds no new blocks.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from doeff_hy_bytecode_guard import _code_store as code_store

PYC = b"\x00" * 16 + b"marshalled code"


def test_two_trees_share_the_stores_one_file_for_the_same_pyc(tmp_path: Path) -> None:
    """When the same-content .pyc is placed into two trees, both become the same inode as the one file in the store (name = sha256 of the content)."""
    store = str(tmp_path / "store")
    first, second = tmp_path / "a" / "__pycache__" / "m.pyc", tmp_path / "b" / "__pycache__" / "m.pyc"
    assert code_store.link_pyc(store, PYC, str(first)) is None
    assert code_store.link_pyc(store, PYC, str(second)) is None
    blob = Path(code_store.entry_path(store, hashlib.sha256(PYC).hexdigest(), code_store.PYC_SUFFIX))
    assert first.read_bytes() == second.read_bytes() == PYC
    assert first.stat().st_ino == second.stat().st_ino == blob.stat().st_ino
    assert blob.stat().st_nlink == 3


def test_the_stores_file_is_read_only_and_a_replaced_tree_pyc_leaves_the_others(tmp_path: Path) -> None:
    """The store's file is read-only (refusing hands that open it in place and write). Even if a tree's .pyc is re-placed with different content (replacement), the other trees' .pyc and the store
    content stay as before."""
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
    """Each time the store's .pyc is used, set its time to now (cleanup deletes what has not been used for 7 days -- the same used-mark as a .code entry)."""
    store = str(tmp_path / "store")
    code_store.link_pyc(store, PYC, str(tmp_path / "a" / "m.pyc"))
    blob = code_store.entry_path(store, hashlib.sha256(PYC).hexdigest(), code_store.PYC_SUFFIX)
    old = 1_000_000_000
    os.utime(blob, (old, old))
    code_store.link_pyc(store, PYC, str(tmp_path / "b" / "m.pyc"))
    assert os.stat(blob).st_mtime > old


def test_placing_the_same_pyc_again_leaves_no_temporary_link(tmp_path: Path) -> None:
    """Do not re-place if the tree's .pyc is already the same file name in the store (renaming onto the same inode does nothing, and a temporary link is left so the names increase by one --
    a reviewer's concern)."""
    store = str(tmp_path / "store")
    target = tmp_path / "a" / "m.pyc"
    assert code_store.link_pyc(store, PYC, str(target)) is None
    assert code_store.link_pyc(store, PYC, str(target)) is None
    assert sorted(p.name for p in target.parent.iterdir()) == ["m.pyc"]
    assert target.stat().st_nlink == 2


def test_a_stored_pyc_left_writable_is_made_read_only_when_used(tmp_path: Path) -> None:
    """A store file left in a writable mode because the writer died before making it read-only is also made read-only when used."""
    store = str(tmp_path / "store")
    code_store.link_pyc(store, PYC, str(tmp_path / "a" / "m.pyc"))
    blob = code_store.entry_path(store, hashlib.sha256(PYC).hexdigest(), code_store.PYC_SUFFIX)
    os.chmod(blob, 0o600)
    assert code_store.link_pyc(store, PYC, str(tmp_path / "b" / "m.pyc")) is None
    assert stat.S_IMODE(os.stat(blob).st_mode) == 0o444


def test_a_second_writer_shares_the_bake_tools_file_through_the_entry_api(tmp_path: Path) -> None:
    """The two writers that place a .pyc from the same entry (the baking tool's form = link_pyc with the entry_pyc bytes; the other writer that gathers .pyc = link_entry_pyc)
    share one file in the store without each holding its own way of assembling it (when the assembly lived only in the baking tool,
    the second writer re-marshaled and made a separate file per root)."""
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
    """When a hardlink cannot be placed in the tree (here a file sits where the tree's dir should be), do not raise but answer a reason that does not include the path (the caller writes the content --
    the reason appears once each in the preparation record)."""
    store = str(tmp_path / "store")
    blocker = tmp_path / "tree"
    blocker.write_text("not a dir")
    problem = code_store.link_pyc(store, PYC, str(blocker / "__pycache__" / "m.pyc"))
    assert problem is not None and "hardlink" in problem, problem
    assert str(tmp_path) not in problem, problem
