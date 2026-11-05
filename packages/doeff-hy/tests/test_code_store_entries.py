"""The code store pulled by source content (:mod:`doeff_hy_bytecode_guard._code_store`) holds several entries per key, one per macro environment; this tests that writers with different environments
do not overwrite each other's entries under the same key.

Real case (2026-10-12, zeus): with one entry per key, the runtime preparation (the worker's baking tool -- record marker 6) and the imports of a conversation's work tree
(the doeff version differs per conversation -- record marker 5, etc.) saw each other's entry under the same source key as "stale", recompiled, and overwrote each other. Preparation
re-baked 100 to 600 files at a time even for the same version of source (0 to 10 before 02:53 JST).

Stand-in for the record: the record is the tuple at the end of the code's constants (:mod:`doeff_hy_bytecode_guard._records`) -- here we append a record-shaped tuple to the end of the code to make two
writers with different records (the check belongs to the caller, so here it checks "whether the trailing tuple is its own").
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import CodeType

import pytest
from doeff_hy_bytecode_guard import _code_store as code_store

#: Two record-shaped tuples (two writers with different doeff versions -- the macro digests differ).
RECORD_FIVE = ("doeff-hy/macro-dependencies/5", "1.1.0", (("_hy_macros", "pkg.macros", "answer", "aa" * 32),), (), (), (), ())
RECORD_SIX = ("doeff-hy/macro-dependencies/6", "1.1.0", (("_hy_macros", "pkg.macros", "answer", "bb" * 32, "1"),), (), (), (), ())

SOURCE = b"(setv value 1)\n"


def _code(record: object, filename: str = "/tree-a/pkg/user.hy") -> CodeType:
    """The code of a module with a record appended to the trailing constants (filename = path of the compiled tree -- differs per tree)."""
    code = compile("value = 1\n", filename, "exec")
    return code.replace(co_consts=(*code.co_consts, record))


def _key() -> str:
    return code_store.code_key("/tree-a/pkg/user.hy", SOURCE, "pkg.user", "1.1.0", "cpython-314", 0)


def _is(record: object):
    """Stand-in for the check -- matches the current environment only when the trailing tuple of the code is record."""
    return lambda code: bool(code.co_consts) and code.co_consts[-1] == record


def _age(path: str, seconds: float) -> None:
    """Make the entry's last-used time seconds seconds ago."""
    past = time.time() - seconds
    os.utime(path, (past, past))


def _record_in(path: str) -> object:
    """The trailing tuple of the entry's code (the stand-in for the record) -- an unreadable entry is a red for the test. The test's read does not mark it used (does not move the time)."""
    code = code_store.stored_code(path, touch=False)
    assert code is not None, f"entry cannot be read as code: {path}"
    return code.co_consts[-1]


def test_writers_with_different_records_keep_their_own_entry_under_one_key(tmp_path: Path) -> None:
    """Even if two writers with different records alternately add to the same key, they do not delete each other's entries, and each can pull code that matches its own environment."""
    store, key = str(tmp_path), _key()
    assert code_store.add_code(store, key, _code(RECORD_FIVE)) is None
    assert code_store.add_code(store, key, _code(RECORD_SIX)) is None
    assert len(code_store.code_entries(store, key)) == 2
    five = code_store.current_code(store, key, _is(RECORD_FIVE))
    six = code_store.current_code(store, key, _is(RECORD_SIX))
    assert five is not None
    assert five.co_consts[-1] == RECORD_FIVE
    assert six is not None
    assert six.co_consts[-1] == RECORD_SIX


def test_the_same_record_from_another_tree_rewrites_its_one_entry(tmp_path: Path) -> None:
    """Code with the same record rewrites a single entry even if the compiled tree (the code's file name) differs -- entries do not multiply per tree."""
    store, key = str(tmp_path), _key()
    code_store.add_code(store, key, _code(RECORD_SIX, "/tree-a/pkg/user.hy"))
    code_store.add_code(store, key, _code(RECORD_SIX, "/tree-b/pkg/user.hy"))
    assert len(code_store.code_entries(store, key)) == 1


def test_records_differing_only_in_a_bytes_or_float_field_keep_separate_entries(tmp_path: Path) -> None:
    """Even if the record fields are bytes or floats (values a later version of the record may add), code with different records gets separate entries -- they do not collapse into one fixed name and overwrite each other."""
    store, key = str(tmp_path), _key()
    for record in ((*RECORD_SIX, b"a"), (*RECORD_SIX, b"b"), (*RECORD_SIX, 1.5)):
        code_store.add_code(store, key, _code(record))
    assert len(code_store.code_entries(store, key)) == 3


def test_a_code_without_a_record_has_one_fixed_entry(tmp_path: Path) -> None:
    """Code whose trailing constant is not record-shaped (code from Python source -- the end may be a function's code) gets one entry even if the trees differ."""
    store, key = str(tmp_path), _key()
    for tree in ("/tree-a", "/tree-b"):
        code_store.add_code(store, key, compile("x = None\ndef f():\n    pass\n", f"{tree}/plain.py", "exec"))
    assert len(code_store.code_entries(store, key)) == 1


def test_only_the_entry_used_moves_its_time(tmp_path: Path) -> None:
    """The time of an entry that was checked and did not match is not advanced -- the order removed at the count cap and the 7-day cleanup are decided only by the uses of the writer that uses the entry."""
    store, key = str(tmp_path), _key()
    code_store.add_code(store, key, _code(RECORD_FIVE))
    code_store.add_code(store, key, _code(RECORD_SIX))
    entries = code_store.code_entries(store, key)
    for path in entries:
        _age(path, 3600)
    before = {path: os.stat(path).st_mtime for path in entries}
    assert code_store.current_code(store, key, _is(RECORD_FIVE)) is not None
    moved = {path for path in entries if os.stat(path).st_mtime != before[path]}
    assert len(moved) == 1
    [used] = moved
    assert code_store.stored_code(used) is not None
    assert code_store.code_entries(store, key)[0] == used, "the used entry did not come to the head of the newest-first order"


def _records(count: int) -> list[tuple[object, ...]]:
    """count record-shaped tuples (all different -- count writers with different macro environments)."""
    return [(*RECORD_SIX[:3], (f"absent-{index}",), (), (), ()) for index in range(count)]


def _entry_of(store: str, key: str, record: object) -> str:
    """The path of the key's entry for record."""
    [path] = [path for path in code_store.code_entries(store, key) if _record_in(path) == record]
    return path


def _read(store: str, key: str, record: object) -> bool:
    """One read in the environment of record (same order as the import entry point and the preparation tool -- use it if the check hits, add one if it misses). Answer = whether it hit."""
    if code_store.current_code(store, key, _is(record)) is not None:
        return True
    code_store.add_code(store, key, _code(record))
    return False


def test_entries_beyond_the_limit_go_least_recently_used_first(tmp_path: Path) -> None:
    """When the key's entries exceed the cap, keep the entry just added and remove from the oldest time. Entries of writers that keep being used remain."""
    store, key = str(tmp_path), _key()
    records = _records(code_store.CODE_ENTRIES_PER_KEY + 1)
    for record in records[:-1]:
        code_store.add_code(store, key, _code(record))
    # Set the used times explicitly: records[1] was used earliest, and records[0] was used most recently (the writer that keeps being used).
    for index, record in enumerate(records[1:-1], start=1):
        _age(_entry_of(store, key, record), 100 * (code_store.CODE_ENTRIES_PER_KEY + 1 - index))
    _age(_entry_of(store, key, records[0]), 10)
    code_store.add_code(store, key, _code(records[-1]))
    kept = code_store.code_entries(store, key)
    assert len(kept) == code_store.CODE_ENTRIES_PER_KEY
    held = {_record_in(path) for path in kept}
    assert records[0] in held, "removed the entry of the writer that keeps being used"
    assert records[-1] in held, "removed the entry just added"
    assert records[1] not in held, "the oldest unused entry remained"


def test_a_new_entry_waits_behind_the_entries_in_use_until_it_is_used(tmp_path: Path) -> None:
    """An entry just added is placed after the key's other entries (its time is earlier) and comes to the head when it is checked and used -- an entry of a one-off environment
    is not placed ahead of the entries of writers that keep being used."""
    store, key = str(tmp_path), _key()
    code_store.add_code(store, key, _code(RECORD_FIVE))
    _age(_entry_of(store, key, RECORD_FIVE), 50)
    code_store.add_code(store, key, _code(RECORD_SIX))
    five, six = _entry_of(store, key, RECORD_FIVE), _entry_of(store, key, RECORD_SIX)
    assert code_store.code_entries(store, key) == (five, six)
    assert os.stat(six).st_mtime < os.stat(five).st_mtime
    assert code_store.current_code(store, key, _is(RECORD_SIX)) is not None
    assert code_store.code_entries(store, key)[0] == six, "the used entry did not come to the head"


def test_one_off_environments_do_not_push_out_an_entry_in_use(tmp_path: Path) -> None:
    """Even if far more one-off environments than the cap (checks in a work tree where macros are being fixed -- a new record every time) add entries, the entries of a writer that keeps being used in between (runtime
    preparation) remain, and that writer's reads keep hitting. Before the fix, when as many one-off environments as the cap added entries, they pushed out the preparation entries
    (blind review A 4(a))."""
    store, key = str(tmp_path), _key()
    prep, *one_offs = _records(1 + 3 * code_store.CODE_ENTRIES_PER_KEY)
    assert not _read(store, key, prep)
    for start in range(0, len(one_offs), code_store.CODE_ENTRIES_PER_KEY):
        for record in one_offs[start : start + code_store.CODE_ENTRIES_PER_KEY]:
            assert not _read(store, key, record)
        assert _read(store, key, prep), "a one-off environment pushed out the entry of a writer that keeps being used"
    assert len(code_store.code_entries(store, key)) <= code_store.CODE_ENTRIES_PER_KEY


def test_more_environments_than_the_limit_taking_turns_miss_only_the_excess(tmp_path: Path) -> None:
    """Even if environments one more than the cap keep reading the same key in turn, from the second round only the excess over the cap misses (it does not become a cliff where everything misses). Before the fix,
    with cap + 1 environments everything missed every round (blind review A 4(a))."""
    store, key = str(tmp_path), _key()
    records = _records(code_store.CODE_ENTRIES_PER_KEY + 1)
    misses = [sum(0 if _read(store, key, record) else 1 for record in records) for _ in range(4)]
    assert misses[0] == len(records)
    assert all(miss <= 2 for miss in misses[1:]), misses


def test_a_broken_entry_is_named_removed_and_the_next_entry_is_used(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A corrupted entry (cannot be read as code) prints a one-line message naming it and is removed, and the next entry is checked (neither silently used nor silently discarded)."""
    store, key = str(tmp_path), _key()
    code_store.add_code(store, key, _code(RECORD_FIVE))
    code_store.add_code(store, key, _code(RECORD_SIX))
    newest = code_store.code_entries(store, key)[0]
    Path(newest).write_bytes(b"\x00broken")
    assert code_store.current_code(store, key, lambda _code: True) is not None
    assert not Path(newest).exists()
    assert "bytecode store entry is corrupted" in capsys.readouterr().err


def test_a_key_without_entries_answers_none(tmp_path: Path) -> None:
    """If the key has no entries at all, the pull is None and the list is empty."""
    store, key = str(tmp_path), _key()
    assert code_store.code_entries(store, key) == ()
    assert code_store.current_code(store, key, lambda _code: True) is None
