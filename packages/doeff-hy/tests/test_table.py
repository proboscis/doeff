"""Checks for the non-mutating table Table (doeff_hy._table) -- an old table keeps reading old rows after writes, and is unchanged after the base is rebuilt.

Failure case: with a table that rewrites its base in place (_InPlaceTable below), a reader holding an old table reads the new rows -- the same check turns red.
"""

from collections.abc import Callable
from itertools import accumulate
from typing import NamedTuple

import hy  # noqa: F401  # import hook for .hy modules
import pytest

from doeff_hy._table import COMPACT_RATIO, MIN_DELTA, Table, TableWrite, draft_of, table_of


def _table(n: int) -> Table[int]:
    """Build a table as test material (keys k0.. -> values 0..)."""
    return table_of(tuple(TableWrite(key=f"k{i}", value=i) for i in range(n)))


def test_an_old_table_keeps_reading_old_rows_after_writes() -> None:
    old = _table(10)
    new = old.with_writes((TableWrite(key="k1", value=100), TableWrite(key="k2", value=None), TableWrite(key="x", value=7)))
    assert (old.row("k1"), old.row("k2"), old.row("x"), old.size()) == (1, 2, None, 10)
    assert (new.row("k1"), new.row("k2"), new.row("x"), new.size()) == (100, None, 7, 10)
    assert sorted(new.keys()) == sorted([f"k{i}" for i in range(10) if i != 2] + ["x"])


def test_old_tables_survive_the_rebuild_of_the_base() -> None:
    base_rows = 1000
    old = _table(base_rows)
    # Write one row at a time until the delta limit (max(MIN_DELTA, base / COMPACT_RATIO)) is exceeded, keeping every intermediate table.
    steps = range(max(MIN_DELTA, base_rows // COMPACT_RATIO) + 5)
    kept = tuple(accumulate(steps, lambda table, i: table.with_writes((TableWrite(key=f"k{i}", value=-i),)), initial=old))[1:]
    assert old.row("k0") == 0 and old.row("k3") == 3
    for i, table in zip(steps, kept):
        assert table.row(f"k{i}") == -i
        assert table.row(f"k{i + 1}") == i + 1
    assert kept[-1].size() == base_rows


def test_writing_back_a_removed_key_restores_it() -> None:
    table = _table(3).with_writes((TableWrite(key="k0", value=None),))
    back = table.with_writes((TableWrite(key="k0", value=9),))
    assert (table.row("k0"), table.size()) == (None, 2)
    assert (back.row("k0"), back.size()) == (9, 3)


def test_a_table_cannot_be_changed() -> None:
    table = _table(1)
    try:
        setattr(table, "_base", {})
    except AttributeError:
        return
    raise AssertionError("a field of Table could be rewritten")


class _InPlaceTable:
    """Failure-case material: a table that rewrites its base in place (only imitates the shape of Table)."""

    def __init__(self, base: dict[str, int]) -> None:
        self._base = base

    def row(self, key: str) -> int | None:
        return self._base.get(key)

    def with_writes(self, writes: tuple[TableWrite[int], ...]) -> "_InPlaceTable":
        for write in writes:
            if write.value is None:
                self._base.pop(write.key, None)
            else:
                self._base[write.key] = write.value
        return self


def test_the_counterexample_an_in_place_table_leaks_new_rows_to_old_readers() -> None:
    old = _InPlaceTable({"k1": 1})
    old.with_writes((TableWrite(key="k1", value=100),))
    assert old.row("k1") == 100  # a reader of the old table sees the new row -- Table does not let this happen (the checks above)


def test_table_is_not_a_mapping() -> None:
    from collections.abc import Mapping

    assert not isinstance(_table(1), Mapping)
    assert isinstance(_table(1), Table)


def test_a_table_and_a_draft_refuse_truth_and_len_and_name_size() -> None:
    """Reading a table or a draft as a truth value or a length is refused with TypeError, naming .size as the fix.
    Counts are read with size (the size of a draft is the number of rows after its writes are applied)."""
    empty = table_of(())
    full = _table(3)
    for value in (empty, full, draft_of(empty), draft_of(full)):
        with pytest.raises(TypeError, match=r"\.size"):
            bool(value)
        with pytest.raises(TypeError, match=r"\.size"):
            len(value)
        with pytest.raises(TypeError, match=r"\.size"):
            _ = not value
    assert (empty.size(), full.size(), draft_of(empty).size()) == (0, 3, 0)
    draft = draft_of(full)
    draft.remove("k0")
    draft.put("x", 9)
    draft.put("k1", 10)
    assert draft.size() == 3 == len(draft.keys())


def test_the_counterexample_a_table_without_bool_reads_true_when_empty() -> None:
    """Failure case (the shape before I0b): a value of a class with neither __bool__ nor __len__ reads as true even when empty -- the early return of `(when (not table) ...)`
    silently has no effect. Table refuses this (the checks above)."""

    class _SilentTable:
        def size(self) -> int:
            return 0

    silent = _SilentTable()
    assert silent.size() == 0 and bool(silent)  # true even though empty -- the oversight does not become an exception


# ------------------------------------------------------------------ Draft TableDraft


def test_a_draft_reads_its_own_writes_and_leaves_the_table_alone() -> None:
    from doeff_hy._table import draft_of

    table = _table(5)
    draft = draft_of(table)
    draft.put("k1", 100)
    draft.remove("k2")
    draft.put("x", 7)
    assert (draft.row("k1"), draft.row("k2"), draft.row("x"), draft.row("k3")) == (100, None, 7, 3)
    assert sorted(draft.keys()) == sorted(["k0", "k1", "k3", "k4", "x"])
    # The original table does not change from the draft's writes (a reader holding an old snapshot keeps reading old rows).
    assert (table.row("k1"), table.row("k2"), table.row("x"), table.size()) == (1, 2, None, 5)
    frozen = draft.freeze()
    assert (frozen.row("k1"), frozen.row("k2"), frozen.row("x"), frozen.size()) == (100, None, 7, 5)
    assert (table.row("k1"), table.size()) == (1, 5)


def test_a_draft_without_writes_freezes_to_the_same_table() -> None:
    from doeff_hy._table import draft_of

    table = _table(3)
    assert draft_of(table).freeze() is table


def test_a_draft_takes_many_writes_in_one_freeze() -> None:
    from doeff_hy._table import draft_of

    # The shape of a listing beat: write tens of thousands of rows into an empty table, and re-read the written rows within the same beat.
    draft = draft_of(table_of(()))
    for i in range(20000):
        draft.put(f"k{i}", i)
        assert draft.row(f"k{i}") == i
    frozen = draft.freeze()
    assert (frozen.size(), frozen.row("k19999"), frozen.row("k0")) == (20000, 19999, 0)


def test_removing_then_putting_back_in_a_draft_restores_the_row() -> None:
    from doeff_hy._table import draft_of

    draft = draft_of(_table(2))
    draft.remove("k0")
    draft.remove("absent")
    draft.put("k0", 9)
    assert (draft.row("k0"), draft.freeze().size()) == (9, 2)


class _ReadThroughDraft:
    """Failure-case material: a draft that accumulates writes but passes reads straight through to the original table (only imitates the shape of TableDraft)."""

    def __init__(self, table: Table[int]) -> None:
        self._table = table
        self._writes: dict[str, int | None] = {}

    def row(self, key: str) -> int | None:
        return self._table.row(key)

    def put(self, key: str, value: int) -> None:
        self._writes[key] = value


def test_the_counterexample_a_read_through_draft_misses_its_own_writes() -> None:
    draft = _ReadThroughDraft(_table(2))
    draft.put("k0", 100)
    assert draft.row("k0") == 0  # a row written within the same beat cannot be read -- TableDraft does not let this happen (the checks above)


# ------------------------------------------------------------------ Sequence of values: rows


def test_rows_follow_the_keys_after_writes_and_removals() -> None:
    from doeff_hy._table import draft_of

    table = _table(5).with_writes((TableWrite(key="k1", value=100), TableWrite(key="k2", value=None), TableWrite(key="x", value=7)))
    assert sorted(table.rows()) == sorted([0, 100, 3, 4, 7])
    assert [table.row(key) for key in table.keys()] == list(table.rows())
    draft = draft_of(table)
    draft.put("k3", 30)
    draft.remove("k4")
    draft.put("y", 8)
    assert sorted(draft.rows()) == sorted([0, 100, 30, 7, 8])
    assert [draft.row(key) for key in draft.keys()] == list(draft.rows())
    # Cross-check for the failure case: a reader that leaves the values of removed rows (k2, k4) in the sequence would have a count that disagrees with the table's size.
    assert len(table.rows()) == table.size() and len(draft.rows()) == draft.freeze().size()


# ------------------------------------------------------------------ Handling as a value


def test_tables_compare_by_rows_not_by_layout() -> None:
    import copy

    written = _table(3).with_writes((TableWrite(key="k1", value=10),))
    built = table_of((TableWrite(key="k0", value=0), TableWrite(key="k1", value=10), TableWrite(key="k2", value=2)))
    # Even if the split between base and delta differs, tables with the same rows are equal.
    assert written == built
    assert written != built.with_writes((TableWrite(key="k2", value=None),))
    assert written != built.with_writes((TableWrite(key="k2", value=3),))
    # Copies (copy, deepcopy) are rebuilt through the constructor and equal the original (a table that refuses field writes can still be copied).
    assert copy.deepcopy(written) == written
    assert copy.copy(written) == written
    assert copy.deepcopy(written) is not written


def test_the_counterexample_an_identity_compared_table_breaks_the_snapshot_check() -> None:
    import copy

    # Cross-check for the failure case: if comparison were by identity (the default object.__eq__), a copy with the same contents would read as "different", and the snapshot check before and after compaction would turn red.
    table = _table(2)
    assert object.__eq__(copy.deepcopy(table), table) is NotImplemented
    assert copy.deepcopy(table) == table


def _reference(base: dict[str, int], writes: list[tuple[str, int | None]]) -> dict[str, int]:
    """A dict with the writes applied one at a time in sequence order (the baseline for comparison) -- to confirm that applying them all at once does not change the meaning of the put/remove order for the same key."""
    out = dict(base)
    for key, value in writes:
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


def _random_writes(seed: int, keys: int, count: int) -> list[tuple[str, int | None]]:
    """Build a random sequence of writes mixing puts and removes for the same keys (keys include both ones in the base and ones not)."""
    import random

    rng = random.Random(seed)
    return [(f"k{rng.randrange(keys)}", None if rng.random() < 0.3 else rng.randrange(1000)) for _ in range(count)]


def test_writes_applied_at_once_match_writes_applied_one_by_one() -> None:
    # with-writes and a draft's freeze apply writes all at once. Varying the base size and the number of writes (both before and after a rebuild),
    # every table has the same rows as a dict with the writes applied one at a time in sequence order.
    from doeff_hy._table import draft_of

    for seed in range(20):
        for base_rows, count in ((0, 10), (40, 30), (1000, 50), (200, 400)):
            base = {f"k{i}": i for i in range(base_rows)}
            # Write on top of a table with earlier writes applied (fewer than the delta limit -- the table stays in two layers, base and delta). Writes that remove rows in the delta are mixed in too.
            earlier = _random_writes(seed + 100, base_rows + 20, 8)
            writes = _random_writes(seed, base_rows + 20, count)
            expected = _reference(_reference(base, earlier), writes)
            table = table_of(tuple(TableWrite(key=k, value=v) for k, v in base.items())).with_writes(
                tuple(TableWrite(key=k, value=v) for k, v in earlier)
            )
            written = table.with_writes(tuple(TableWrite(key=k, value=v) for k, v in writes))
            draft = draft_of(table)
            for key, value in writes:
                if value is None:
                    draft.remove(key)
                else:
                    draft.put(key, value)
            frozen = draft.freeze()
            for got in (written, frozen):
                assert dict(zip(got.keys(), got.rows())) == expected, (seed, base_rows, count)
                assert got.size() == len(expected), (seed, base_rows, count)
                assert all(got.row(k) == expected.get(k) for k in {k for k, _ in writes} | set(base)), (seed, base_rows, count)


# ------------------------------------------------------------------ Fast path (I0a of #2708 -- the contract is unchanged)


class _UnreadableRow:
    """Check material: a row that stops when compared (to confirm that comparing the same table does not read the contents)."""

    def __eq__(self, other: object) -> bool:
        raise AssertionError("comparing the same table read the contents of a row")

    __hash__ = None  # type: ignore[assignment]


def test_the_same_table_compares_equal_without_reading_rows() -> None:
    table = table_of((TableWrite(key="k", value=_UnreadableRow()),))
    assert table == table
    # Failure case: a different table with the same contents is compared by reading the rows (without the shortcut for the same object, the comparison above would stop here too).
    copy = table_of((TableWrite(key="k", value=table.row("k")),))
    try:
        _ = table == copy
    except AssertionError:
        return
    raise AssertionError("comparing a different table did not read the rows")


def test_a_small_table_keeps_rows_and_order_without_a_delta_layer() -> None:
    old = _table(MIN_DELTA - 1)
    new = old.with_writes((TableWrite(key="k1", value=100), TableWrite(key="k2", value=None), TableWrite(key="x", value=7)))
    assert (old.row("k1"), old.row("k2"), old.size()) == (1, 2, MIN_DELTA - 1)
    assert (new.row("k1"), new.row("k2"), new.row("x"), new.size()) == (100, None, 7, MIN_DELTA - 1)
    assert new.keys() == tuple(key for key in old.keys() if key != "k2") + ("x",)
    assert new.rows() == tuple(new.row(key) for key in new.keys())
    # A small table has no delta layer (each write copies the base -- it does not pay for a full copy of the delta dict).
    assert (new._delta, new._removed) == ({}, frozenset())


def test_items_pair_the_keys_with_the_rows() -> None:
    small = _table(5).with_writes((TableWrite(key="k0", value=None), TableWrite(key="y", value=9)))
    big = _table(MIN_DELTA * 2).with_writes((TableWrite(key="k3", value=-3), TableWrite(key="k4", value=None)))
    for table in (small, big):
        assert table.items() == tuple(zip(table.keys(), table.rows()))
        assert all(table.row(key) == value for key, value in table.items())
    assert big._delta  # a big table goes through the delta-layer path (confirms that items builds including the delta)


# ------------------------------------------------------------------ Comparing tables that share a base (comparing copies of the board was proportional to the number of cards)


class _CountedRow:
    """Check material: a row that counts how many times it was compared (to confirm that comparing tables sharing a base reads only the rows of written keys)."""

    compared = 0

    def __init__(self, value: int) -> None:
        self.value = value

    def __eq__(self, other: object) -> bool:
        _CountedRow.compared += 1
        return isinstance(other, _CountedRow) and self.value == other.value

    __hash__ = None  # type: ignore[assignment]


def _counted_table(n: int) -> Table[_CountedRow]:
    return table_of(tuple(TableWrite(key=f"k{i}", value=_CountedRow(i)) for i in range(n)))


class _Comparison(NamedTuple):
    """The result of comparing two tables: whether they are equal, and the number of rows read to compare them."""

    equal: bool
    rows_read: int


def _rows_read_by(left: Table[_CountedRow], right: Table[_CountedRow]) -> _Comparison:
    _CountedRow.compared = 0
    answer = left == right
    return _Comparison(answer, _CountedRow.compared)


def test_tables_sharing_a_base_compare_only_the_written_keys() -> None:
    base = _counted_table(MIN_DELTA * 20)
    moved = base.with_writes((TableWrite(key="k100", value=_CountedRow(-100)),))
    moved_again = moved.with_writes((TableWrite(key="k200", value=_CountedRow(-200)),))
    assert moved._base is moved_again._base  # premise: with-writes shares the base (while the delta is small)
    # Two tables sharing a base: does not read the base's 1,280 rows, compares only the rows of the written keys (k100, k200).
    assert _rows_read_by(moved, moved_again) == (False, 1)
    assert _rows_read_by(moved_again, moved).equal is False
    # A table in which a row was rewritten with the same contents (a different object) is also judged "equal" from the written key's row alone.
    rewritten = base.with_writes((TableWrite(key="k100", value=_CountedRow(100)),))
    assert _rows_read_by(base, rewritten) == (True, 1)
    # Cross-check for the failure case: without the shared-base path (comparing with a table whose base was rebuilt), all rows are read and compared.
    rebuilt = _counted_table(MIN_DELTA * 20)
    assert rebuilt._base is not base._base
    assert _rows_read_by(base, rebuilt) == (True, MIN_DELTA * 20)


def test_tables_sharing_a_base_compare_removed_and_added_keys() -> None:
    base = _table(MIN_DELTA * 4)
    removed = base.with_writes((TableWrite(key="k3", value=None),))
    added = base.with_writes((TableWrite(key="new", value=1),))
    assert removed._base is added._base is base._base
    # A base row removed by only one side and a row added by only one side make the key sets differ, so they are not equal (even if the row counts are the same).
    assert removed != added
    assert removed != base
    assert added != base
    # A table that removed a key and added it back with the same value equals the base.
    restored = removed.with_writes((TableWrite(key="k3", value=3),))
    assert restored == base
    assert restored != removed
    # The answer does not change between a table that does not share a base (one with a rebuilt base) and a table that does.
    assert base == table_of(tuple(TableWrite(key=f"k{i}", value=i) for i in range(MIN_DELTA * 4)))
    assert removed != table_of(tuple(TableWrite(key=f"k{i}", value=i) for i in range(MIN_DELTA * 4)))


def test_comparing_tables_agrees_with_comparing_their_rows_for_any_writes() -> None:
    # Comparing two tables that share a base (reading only the delta and removed keys) agrees with the answer from comparing all rows as dicts.
    # Mix puts, removes, and re-puts of the same value as the base for the same keys, and include write counts where the delta exceeds 1/COMPACT-RATIO of the base and the base is rebuilt.
    for seed in range(40):
        for base_rows, count in ((MIN_DELTA, 6), (MIN_DELTA * 4, 20), (MIN_DELTA * 4, 400)):
            base = {f"k{i}": i for i in range(base_rows)}
            table = table_of(tuple(TableWrite(key=k, value=v) for k, v in base.items()))
            first = _random_writes(seed, base_rows + 10, count)
            # Mix in writes that re-put the same value as the base (writes that do not change the contents).
            second = _random_writes(seed + 1000, base_rows + 10, count) + [(f"k{seed % base_rows}", seed % base_rows)]
            left = table.with_writes(tuple(TableWrite(key=k, value=v) for k, v in first))
            right = table.with_writes(tuple(TableWrite(key=k, value=v) for k, v in second))
            left_rows = _reference(base, first)
            right_rows = _reference(base, second)
            assert (left == right) is (left_rows == right_rows), (seed, base_rows, count)
            assert (left != right) is (left_rows != right_rows), (seed, base_rows, count)
            # Comparison of a table with writes added against the table before adding (a mix of ones still sharing the base and ones whose base was rebuilt).
            more = _random_writes(seed + 2000, base_rows + 10, 3)
            longer = left.with_writes(tuple(TableWrite(key=k, value=v) for k, v in more))
            assert (left == longer) is (left_rows == _reference(left_rows, more)), (seed, base_rows, count)


# ------------------------------------------------------------------ Keys whose rows differ (the screen puts the keys of the board's changed cards into a signal)


def _changed_reference(left: dict[str, int], right: dict[str, int]) -> tuple[str, ...]:
    """The keys whose values differ between two dicts (including keys present in only one side, in ascending order) -- the baseline for comparing the answer of changed-keys."""
    return tuple(sorted(key for key in left.keys() | right.keys() if left.get(key) != right.get(key)))


def test_changed_keys_names_written_removed_and_added_keys_only() -> None:
    base = _table(MIN_DELTA * 4)
    moved = base.with_writes((
        TableWrite(key="k1", value=-1), TableWrite(key="k2", value=None), TableWrite(key="new", value=7),
        TableWrite(key="k3", value=3),  # a rewrite with the same value is not reported
    ))
    assert moved._base is base._base  # premise: the shared-base path
    assert moved.changed_keys(base) == ("k1", "k2", "new")
    assert base.changed_keys(moved) == ("k1", "k2", "new")
    assert base.changed_keys(base) == ()
    # Equal tables give empty (the same reading as __eq__)
    assert moved.changed_keys(moved.with_writes((TableWrite(key="k1", value=-1),))) == ()


def test_changed_keys_of_tables_sharing_a_base_reads_only_the_written_rows() -> None:
    base = _counted_table(MIN_DELTA * 20)
    moved = base.with_writes((TableWrite(key="k100", value=_CountedRow(-100)),))
    moved_again = moved.with_writes((TableWrite(key="k200", value=_CountedRow(-200)), TableWrite(key="k300", value=_CountedRow(300))))
    assert moved._base is moved_again._base
    _CountedRow.compared = 0
    # k300 is a rewrite with the same value (a different object) -- compared by value and not reported. The base's 1,280 rows are not read
    assert moved.changed_keys(moved_again) == ("k200",)
    assert _CountedRow.compared <= 3, "comparing tables sharing a base read rows outside the written keys"
    # Cross-check for the failure case: comparing with a table whose base was rebuilt reads all keys (rows covered by the identity shortcut do not read the value)
    rebuilt = _counted_table(MIN_DELTA * 20)
    _CountedRow.compared = 0
    assert base.changed_keys(rebuilt) == ()
    assert _CountedRow.compared == MIN_DELTA * 20


def test_changed_keys_agrees_with_comparing_the_rows_for_any_writes() -> None:
    # With both tables sharing a base (reading only the delta and removed keys) and tables whose base was rebuilt (reading all keys), the result agrees with the difference of the full-row dicts.
    for seed in range(40):
        for base_rows, count in ((MIN_DELTA, 6), (MIN_DELTA * 4, 20), (MIN_DELTA * 4, 400)):
            base = {f"k{i}": i for i in range(base_rows)}
            table = table_of(tuple(TableWrite(key=k, value=v) for k, v in base.items()))
            first = _random_writes(seed, base_rows + 10, count)
            second = _random_writes(seed + 1000, base_rows + 10, count)
            left = table.with_writes(tuple(TableWrite(key=k, value=v) for k, v in first))
            right = table.with_writes(tuple(TableWrite(key=k, value=v) for k, v in second))
            expected = _changed_reference(_reference(base, first), _reference(base, second))
            assert left.changed_keys(right) == expected, (seed, base_rows, count)
            assert (left.changed_keys(right) == ()) is (left == right), (seed, base_rows, count)


# ------------------------------------------------------------------ Per-unit-cost fixes (card E0 -- counting rows, reading all rows, empty writes, refusing ==)


def _model_check(table: Table[int], model: dict[str, int], where: object) -> None:
    assert table.size() == len(model), where
    assert all(table.row(k) == v for k, v in model.items()), where
    assert table.row("absent") is None, where
    keys, rows, items = table.keys(), table.rows(), table.items()
    assert set(keys) == set(model) and len(keys) == len(model), where
    assert dict(zip(keys, rows)) == model, where
    # keys, rows, and items are in the same order.
    assert items == tuple(zip(keys, rows)), where


def test_random_writes_removes_and_compactions_match_a_dict_model() -> None:
    import random

    for seed in range(30):
        rng = random.Random(seed)
        model: dict[str, int] = {f"k{i}": i for i in range(rng.choice((0, 5, 80, 300)))}
        table = table_of(tuple(TableWrite(key=k, value=v) for k, v in model.items()))
        for step in range(40):
            # Mix small writes (staying in two layers) and large writes (rebuilding the base).
            count = rng.choice((1, 2, 5, 40, 120))
            writes = _random_writes(rng.randrange(10**6), len(model) + 30, count)
            if rng.random() < 0.5:
                table = table.with_writes(tuple(TableWrite(key=k, value=v) for k, v in writes))
            else:
                draft = draft_of(table)
                for k, v in writes:
                    draft.remove(k) if v is None else draft.put(k, v)
                table = draft.freeze()
            model = _reference(model, writes)
            _model_check(table, model, (seed, step))


def test_size_does_not_walk_the_base() -> None:
    class _CountingDict(dict):  # type: ignore[type-arg]
        iterated = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            _CountingDict.iterated += 1
            return super().__iter__()

    base = _CountingDict({f"k{i}": i for i in range(1000)})
    table = Table(base, {"k1": 5, "new": 1}, frozenset({"k2"}))
    assert table.size() == 1000 - 1 + 1
    assert _CountingDict.iterated == 0


def test_all_rows_are_read_from_one_copy_per_table() -> None:
    big = _table(MIN_DELTA * 4).with_writes((TableWrite(key="k3", value=-3), TableWrite(key="k4", value=None), TableWrite(key="z", value=1)))
    assert big._delta and big._removed
    assert big._flat is None
    first = big.keys()
    flat = big._flat
    assert flat is not None and "k4" not in flat and flat["k3"] == -3
    big.rows(), big.items()
    assert big._flat is flat  # from the second time on, the same copy
    assert first == big.keys()
    # A table of the base alone is the base itself (not copied).
    plain = _table(MIN_DELTA * 4)
    plain.rows()
    assert plain._flat is plain._base


def test_writes_that_change_nothing_return_the_same_table() -> None:
    row = object()
    for table in (_table(5), _table(MIN_DELTA * 4).with_writes((TableWrite(key="k1", value=-1),))):
        assert table.with_writes(()) is table
        assert table.with_writes((TableWrite(key="k2", value=table.row("k2")),)) is table
        assert table.with_writes((TableWrite(key="absent", value=None),)) is table
        assert table.with_writes((TableWrite(key="k2", value=table.row("k2")), TableWrite(key="k2", value=table.row("k2")))) is table
        assert table.with_writes((TableWrite(key="k2", value=999),)) is not table
    # Even with the same value, a different object gives a new table (judged by identity).
    t = table_of((TableWrite(key="k", value=[1]),))
    assert t.with_writes((TableWrite(key="k", value=[1]),)) is not t
    assert row is not None


def test_a_draft_putting_the_same_rows_freezes_to_the_original_table() -> None:
    for table in (_table(5), _table(MIN_DELTA * 4).with_writes((TableWrite(key="k1", value=-1),))):
        draft = draft_of(table)
        draft.put("k2", table.row("k2"))  # type: ignore[arg-type]
        draft.put("k1", table.row("k1"))  # type: ignore[arg-type]
        assert draft.freeze() is table
        draft.remove("absent")
        assert draft.freeze() is table


def test_comparing_a_table_with_a_dict_is_refused() -> None:
    from collections import OrderedDict

    table = _table(3)
    for other in ({"k0": 0, "k1": 1, "k2": 2}, OrderedDict(), {}):
        for compare in (lambda a, b: a == b, lambda a, b: b == a, lambda a, b: a != b):
            with pytest.raises(TypeError):
                compare(table, other)
    # Comparison with something that is not a mapping is false as before.
    assert (table == None) is False  # noqa: E711
    assert (table == 3) is False


def test_copy_and_pickle_keep_the_rows_and_drop_the_flat_copy() -> None:
    import copy
    import pickle

    table = _table(MIN_DELTA * 4).with_writes((TableWrite(key="k3", value=-3), TableWrite(key="k4", value=None)))
    table.keys()  # even after the copy has been made
    for back in (copy.copy(table), copy.deepcopy(table), pickle.loads(pickle.dumps(table))):
        assert back == table and back.keys() == table.keys() and back.items() == table.items()
        assert back._flat is None or back._flat is not table._flat


# ------------------------------------------------------------------ Lineage (comparing with a descendant is proportional to the number of writes)


def _rows_looked_up(monkeypatch: pytest.MonkeyPatch, compare: Callable[[], object]) -> int:
    """Count how many times comparison compare looked up rows of a table (the number of rows looked up = the work of the comparison)."""
    looked = [0]
    row = Table.row

    def counted(self: Table[object], key: str) -> object:
        looked[0] += 1
        return row(self, key)

    monkeypatch.setattr(Table, "row", counted)
    compare()
    monkeypatch.setattr(Table, "row", row)
    return looked[0]


def _grown_chain(rows: int, writes: int) -> Table[int]:
    """Build a table whose delta layer is grown by continuing writes of one key at a time, writes times (the shape of per-beat compaction in production)."""
    table = _table(rows)
    for step in range(writes):
        table = table.with_writes((TableWrite(key=f"k{(step * 37) % rows}", value=-step - 1),))
    return table


def test_comparing_a_descendant_reads_only_the_rows_written_between(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = MIN_DELTA * 20
    grown = _grown_chain(rows, rows // COMPACT_RATIO - 2)
    assert len(grown._delta) > 100  # premise: the delta layer has grown
    moved = grown.with_writes((TableWrite(key="k5", value=-5000),))
    assert moved._base is grown._base
    # Does not read the delta layer's 150 rows; looks up only the 1 key written between the two (once each in the tables before and after).
    assert _rows_looked_up(monkeypatch, lambda: moved.changed_keys(grown)) <= 2
    assert _rows_looked_up(monkeypatch, lambda: moved == grown) <= 2
    assert moved.changed_keys(grown) == ("k5",) and grown.changed_keys(moved) == ("k5",)
    assert (moved == grown) is False
    # Comparison with an ancestor also reads only the keys written in between.
    older = _grown_chain(rows, 3)
    assert set(moved.changed_keys(older)) <= {f"k{(step * 37) % rows}" for step in range(rows)} | {"k5"}


def test_the_counterexample_a_sibling_compares_the_whole_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    # A second child made from the same parent is not on the lineage -- comparing tables that are not descendants goes through the former path (the whole delta layer), and the answer is correct.
    rows = MIN_DELTA * 20
    grown = _grown_chain(rows, rows // COMPACT_RATIO - 2)
    first = grown.with_writes((TableWrite(key="k5", value=-5000),))
    second = grown.with_writes((TableWrite(key="k6", value=-6000),))
    assert first._line is grown._line and second._line is not grown._line
    assert first.changed_keys(second) == ("k5", "k6")
    assert _rows_looked_up(monkeypatch, lambda: first.changed_keys(second)) > 100


def test_comparing_across_the_rebuild_of_the_base_reads_only_the_written_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = MIN_DELTA * 20
    grown = _grown_chain(rows, rows // COMPACT_RATIO)
    rebuilt = grown.with_writes((TableWrite(key="k7", value=-7000), TableWrite(key="k8", value=-8000)))
    assert rebuilt._base is not grown._base  # premise: this write rebuilt the base
    assert _rows_looked_up(monkeypatch, lambda: rebuilt.changed_keys(grown)) <= 4
    assert rebuilt.changed_keys(grown) == ("k7", "k8")


def test_chains_siblings_drafts_and_line_breaks_compare_like_their_rows() -> None:
    import random

    for seed in range(20):
        rng = random.Random(seed)
        model = {f"k{i}": i for i in range(rng.choice((5, 80, 300)))}
        start = table_of(tuple(TableWrite(key=k, value=v) for k, v in model.items()))
        kept: list[tuple[Table[int], dict[str, int]]] = [(start, model)]
        for step in range(120):
            # Mostly a child of the last table (on the lineage); sometimes a child of an old table (a second child -- a new lineage).
            table, rows = kept[-1] if rng.random() < 0.8 else rng.choice(kept)
            writes = _random_writes(rng.randrange(10**6), len(rows) + 20, rng.choice((1, 2, 5, 40)))
            if rng.random() < 0.5:
                child = table.with_writes(tuple(TableWrite(key=k, value=v) for k, v in writes))
            else:
                draft = draft_of(table)
                for k, v in writes:
                    draft.remove(k) if v is None else draft.put(k, v)
                child = draft.freeze()
            kept = [*kept, (child, _reference(rows, writes))]
        for _ in range(200):
            (left, left_rows), (right, right_rows) = rng.choice(kept), rng.choice(kept)
            expected = _changed_reference(left_rows, right_rows)
            assert left.changed_keys(right) == expected, seed
            assert (left == right) is (left_rows == right_rows), seed


def test_a_line_keeps_no_more_keys_than_its_table_holds() -> None:
    table = _grown_chain(MIN_DELTA * 2, MIN_DELTA * 10)
    assert table._line.keys <= max(MIN_DELTA, table.size())


def test_small_tables_share_the_empty_layers_and_hold_no_line_until_a_child() -> None:
    # Small per-row tables (450,000 on the screen) do not hold an empty delta, empty removed keys, or a lineage per table.
    first, second = _table(3), _table(4)
    assert first._delta is second._delta and first._removed is second._removed
    assert first._line is None and second._line is None
    child = first.with_writes((TableWrite(key="k0", value=-1),))
    assert child._delta is first._delta and child._removed is first._removed
    # The first child gives the parent a lineage, and the child is on it (comparison is only the written keys). A second child is not on it.
    assert first._line is not None and child._line is first._line
    sibling = first.with_writes((TableWrite(key="k1", value=-2),))
    assert sibling._line is None
    assert child.changed_keys(sibling) == ("k0", "k1") and (child == sibling) is False
    assert first.changed_keys(second) == ("k3",) and second.changed_keys(_table(4)) == ()
    # The answer of a write that emptied the delta layer (rebuilding the base) also holds the shared empty values.
    rebuilt = _grown_chain(MIN_DELTA * 20, MIN_DELTA * 20 // COMPACT_RATIO + 1)
    assert rebuilt._delta is first._delta and rebuilt._removed is first._removed


def test_first_children_made_at_once_from_threads_give_the_parent_one_line() -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    for _ in range(50):
        parent = _table(5)
        barrier = Barrier(8)

        def write(step: int, parent: Table[int] = parent, barrier: Barrier = barrier) -> Table[int]:
            barrier.wait()
            return parent.with_writes((TableWrite(key=f"k{step % 5}", value=-step - 1),))

        with ThreadPoolExecutor(8) as pool:
            children = list(pool.map(write, range(8)))
        assert sum(child._line is parent._line for child in children) == 1
        for child in children:
            for other in children:
                assert (child == other) is (child is other)
