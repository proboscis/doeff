"""書き換えない表 Table(doeff_hy._table)の検 — 古い表は書きの後も古い行を読み、基の作り直しの後も変わらない。

失敗ケース: 基をその場で書き換える表(下の _InPlaceTable)だと、古い表を持つ読み手が新しい行を読む — 同じ検が赤になる。
"""

from collections.abc import Callable
from itertools import accumulate
from typing import NamedTuple

import hy  # noqa: F401  # .hy の module の import hook
import pytest

from doeff_hy._table import COMPACT_RATIO, MIN_DELTA, Table, TableWrite, draft_of, table_of


def _table(n: int) -> Table[int]:
    """検の材料の表を作るため(鍵 k0.. → 値 0..)。"""
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
    # 差分の上限(max(MIN_DELTA, 基 / COMPACT_RATIO))を越えるまで 1 行ずつ書き、途中の表を全部持っておく。
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
    raise AssertionError("Table の欄を書き換えられた")


class _InPlaceTable:
    """失敗ケースの材料: 基をその場で書き換える表(Table の形を真似るだけ)。"""

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
    assert old.row("k1") == 100  # 古い表の読み手が新しい行を見る — Table はこれを起こさない(上の検)


def test_table_is_not_a_mapping() -> None:
    from collections.abc import Mapping

    assert not isinstance(_table(1), Mapping)
    assert isinstance(_table(1), Table)


def test_a_table_and_a_draft_refuse_truth_and_len_and_name_size() -> None:
    """表と下書きを真偽・長さで読むと TypeError で断り、直し方の .size を名指す。
    数は size で読む(下書きの size は書きを当てた後の行の数)。"""
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
    """失敗ケース(I0b の前の形): __bool__ も __len__ も持たない class の値は、空でも真と読まれる — `(when (not table) …)` の
    早い戻りが黙って効かない。Table はこれを断る(上の検)。"""

    class _SilentTable:
        def size(self) -> int:
            return 0

    silent = _SilentTable()
    assert silent.size() == 0 and bool(silent)  # 空なのに真 — 見落としが例外にならない


# ------------------------------------------------------------------ 下書き TableDraft


def test_a_draft_reads_its_own_writes_and_leaves_the_table_alone() -> None:
    from doeff_hy._table import draft_of

    table = _table(5)
    draft = draft_of(table)
    draft.put("k1", 100)
    draft.remove("k2")
    draft.put("x", 7)
    assert (draft.row("k1"), draft.row("k2"), draft.row("x"), draft.row("k3")) == (100, None, 7, 3)
    assert sorted(draft.keys()) == sorted(["k0", "k1", "k3", "k4", "x"])
    # 元の表は下書きの書きで変わらない(古い断面を持つ読み手は古い行を読み続ける)。
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

    # 一覧の拍の形: 空の表へ数万件を書き、書いた行を同じ拍の中で読み直す。
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
    """失敗ケースの材料: 書きを貯めるが、読みは元の表へ素通りする下書き(TableDraft の形を真似るだけ)。"""

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
    assert draft.row("k0") == 0  # 同じ拍の中で書いた行が読めない — TableDraft はこれを起こさない(上の検)


# ------------------------------------------------------------------ 値の列 rows


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
    # 失敗ケースの照らし: 消した行(k2・k4)の値を列に残す読み手なら、数えが表の size と食い違う。
    assert len(table.rows()) == table.size() and len(draft.rows()) == draft.freeze().size()


# ------------------------------------------------------------------ 値としての扱い


def test_tables_compare_by_rows_not_by_layout() -> None:
    import copy

    written = _table(3).with_writes((TableWrite(key="k1", value=10),))
    built = table_of((TableWrite(key="k0", value=0), TableWrite(key="k1", value=10), TableWrite(key="k2", value=2)))
    # 基と差分の分け方が違っても、行が同じなら等しい。
    assert written == built
    assert written != built.with_writes((TableWrite(key="k2", value=None),))
    assert written != built.with_writes((TableWrite(key="k2", value=3),))
    # 写し(copy・deepcopy)は作る口を通して作り直され、元と等しい(欄の書きを断る表でも写せる)。
    assert copy.deepcopy(written) == written
    assert copy.copy(written) == written
    assert copy.deepcopy(written) is not written


def test_the_counterexample_an_identity_compared_table_breaks_the_snapshot_check() -> None:
    import copy

    # 失敗ケースの照らし: 比べが同一性(既定の object.__eq__)なら、中身の同じ写しが「違う」と読まれ、畳みの前後の断面の検が赤になる。
    table = _table(2)
    assert object.__eq__(copy.deepcopy(table), table) is NotImplemented
    assert copy.deepcopy(table) == table


def _reference(base: dict[str, int], writes: list[tuple[str, int | None]]) -> dict[str, int]:
    """書きを列の順に 1 件ずつ当てた dict(比べの元)— 1 度に当てる形が、同じ鍵の置く・消すの順の意味を変えていない事を確かめるため。"""
    out = dict(base)
    for key, value in writes:
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


def _random_writes(seed: int, keys: int, count: int) -> list[tuple[str, int | None]]:
    """同じ鍵に置く・消すを混ぜた無作為の書きの列を作るため(鍵は基に在る物と無い物の両方)。"""
    import random

    rng = random.Random(seed)
    return [(f"k{rng.randrange(keys)}", None if rng.random() < 0.3 else rng.randrange(1000)) for _ in range(count)]


def test_writes_applied_at_once_match_writes_applied_one_by_one() -> None:
    # with-writes と下書きの freeze は書きを 1 度に当てる。基の大きさ・書きの数を変え(作り直しの前と後の両方)、
    # どの表も、列の順に 1 件ずつ当てた dict と同じ行を持つ。
    from doeff_hy._table import draft_of

    for seed in range(20):
        for base_rows, count in ((0, 10), (40, 30), (1000, 50), (200, 400)):
            base = {f"k{i}": i for i in range(base_rows)}
            # 前の書き(差分の上限より少ない — 表は基と差分の 2 層のまま)を当てた表に重ねて書く。差分に在る行を消す書きも混ざる。
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


# ------------------------------------------------------------------ 速さの道(#2708 の I0a・契約は変えない)


class _UnreadableRow:
    """検の材料: 比べられると止まる行(同じ表の比べが中身を読まない事を確かめるため)。"""

    def __eq__(self, other: object) -> bool:
        raise AssertionError("同じ表の比べが行の中身を読んだ")

    __hash__ = None  # type: ignore[assignment]


def test_the_same_table_compares_equal_without_reading_rows() -> None:
    table = table_of((TableWrite(key="k", value=_UnreadableRow()),))
    assert table == table
    # 失敗ケース: 中身の同じ別の表は行を読んで比べる(同じ object の近道を外すと、上の比べもここと同じく止まる)。
    copy = table_of((TableWrite(key="k", value=table.row("k")),))
    try:
        _ = table == copy
    except AssertionError:
        return
    raise AssertionError("別の表の比べが行を読まなかった")


def test_a_small_table_keeps_rows_and_order_without_a_delta_layer() -> None:
    old = _table(MIN_DELTA - 1)
    new = old.with_writes((TableWrite(key="k1", value=100), TableWrite(key="k2", value=None), TableWrite(key="x", value=7)))
    assert (old.row("k1"), old.row("k2"), old.size()) == (1, 2, MIN_DELTA - 1)
    assert (new.row("k1"), new.row("k2"), new.row("x"), new.size()) == (100, None, 7, MIN_DELTA - 1)
    assert new.keys() == tuple(key for key in old.keys() if key != "k2") + ("x",)
    assert new.rows() == tuple(new.row(key) for key in new.keys())
    # 小さな表は差分の層を持たない(書きごとに基を写す — 差分の dict の丸写しを払わない)。
    assert (new._delta, new._removed) == ({}, frozenset())


def test_items_pair_the_keys_with_the_rows() -> None:
    small = _table(5).with_writes((TableWrite(key="k0", value=None), TableWrite(key="y", value=9)))
    big = _table(MIN_DELTA * 2).with_writes((TableWrite(key="k3", value=-3), TableWrite(key="k4", value=None)))
    for table in (small, big):
        assert table.items() == tuple(zip(table.keys(), table.rows()))
        assert all(table.row(key) == value for key, value in table.items())
    assert big._delta  # 大きな表は差分の層の道を通っている(items が差分を含めて組む事の確かめ)


# ------------------------------------------------------------------ 基を分け合う表の比べ(盤の写しの比べが card の数に比例していた)


class _CountedRow:
    """検の材料: 比べられた回数を数える行(基を分け合う表の比べが、書いた鍵の行だけを読む事を確かめるため)。"""

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
    """2 つの表の比べの結果: 等しいか、比べるために読んだ行の数。"""

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
    assert moved._base is moved_again._base  # 前提: with-writes は基を分け合う(差分が小さい間)
    # 基を分け合う 2 つの表: 基の 1,280 行を読まず、書いた鍵(k100・k200)の行だけを比べる。
    assert _rows_read_by(moved, moved_again) == (False, 1)
    assert _rows_read_by(moved_again, moved).equal is False
    # 中身の同じ行を書き直した表(別の object)も、書いた鍵の行だけで「等しい」と判じる。
    rewritten = base.with_writes((TableWrite(key="k100", value=_CountedRow(100)),))
    assert _rows_read_by(base, rewritten) == (True, 1)
    # 失敗ケースの照らし: 基を分け合う道が無ければ(基を作り直した表との比べ)、全行を読んで比べる。
    rebuilt = _counted_table(MIN_DELTA * 20)
    assert rebuilt._base is not base._base
    assert _rows_read_by(base, rebuilt) == (True, MIN_DELTA * 20)


def test_tables_sharing_a_base_compare_removed_and_added_keys() -> None:
    base = _table(MIN_DELTA * 4)
    removed = base.with_writes((TableWrite(key="k3", value=None),))
    added = base.with_writes((TableWrite(key="new", value=1),))
    assert removed._base is added._base is base._base
    # 片方だけが消した基の行・片方だけが足した行は、鍵の集合が違うので等しくない(行の数が同じでも)。
    assert removed != added
    assert removed != base
    assert added != base
    # 消して同じ値で足し直した表は、基と等しい。
    restored = removed.with_writes((TableWrite(key="k3", value=3),))
    assert restored == base
    assert restored != removed
    # 基を分け合わない表(基を作り直した物)と、基を分け合う表で答えが変わらない。
    assert base == table_of(tuple(TableWrite(key=f"k{i}", value=i) for i in range(MIN_DELTA * 4)))
    assert removed != table_of(tuple(TableWrite(key=f"k{i}", value=i) for i in range(MIN_DELTA * 4)))


def test_comparing_tables_agrees_with_comparing_their_rows_for_any_writes() -> None:
    # 基を分け合う 2 つの表の比べ(差分と消した鍵だけを読む)が、行の全部を dict にして比べた答えと一致する。
    # 同じ鍵への置く・消す・基と同じ値の置き直しを混ぜ、差分が基の 1/COMPACT-RATIO を越えて基が作り直される書きの数も含める。
    for seed in range(40):
        for base_rows, count in ((MIN_DELTA, 6), (MIN_DELTA * 4, 20), (MIN_DELTA * 4, 400)):
            base = {f"k{i}": i for i in range(base_rows)}
            table = table_of(tuple(TableWrite(key=k, value=v) for k, v in base.items()))
            first = _random_writes(seed, base_rows + 10, count)
            # 基と同じ値を置き直す書きを混ぜる(中身が変わらない書き)。
            second = _random_writes(seed + 1000, base_rows + 10, count) + [(f"k{seed % base_rows}", seed % base_rows)]
            left = table.with_writes(tuple(TableWrite(key=k, value=v) for k, v in first))
            right = table.with_writes(tuple(TableWrite(key=k, value=v) for k, v in second))
            left_rows = _reference(base, first)
            right_rows = _reference(base, second)
            assert (left == right) is (left_rows == right_rows), (seed, base_rows, count)
            assert (left != right) is (left_rows != right_rows), (seed, base_rows, count)
            # 書きを足した表と、足す前の表の比べ(基を分け合ったままの物と、基が作り直された物の両方が混じる)。
            more = _random_writes(seed + 2000, base_rows + 10, 3)
            longer = left.with_writes(tuple(TableWrite(key=k, value=v) for k, v in more))
            assert (left == longer) is (left_rows == _reference(left_rows, more)), (seed, base_rows, count)


# ------------------------------------------------------------------ 行が違う鍵(画面が盤の変わった card の鍵を合図に載せる)


def _changed_reference(left: dict[str, int], right: dict[str, int]) -> tuple[str, ...]:
    """2 つの dict で値が違う鍵(片方にだけ在る鍵を含む・鍵の昇順)— changed-keys の答えの比べの元。"""
    return tuple(sorted(key for key in left.keys() | right.keys() if left.get(key) != right.get(key)))


def test_changed_keys_names_written_removed_and_added_keys_only() -> None:
    base = _table(MIN_DELTA * 4)
    moved = base.with_writes((
        TableWrite(key="k1", value=-1), TableWrite(key="k2", value=None), TableWrite(key="new", value=7),
        TableWrite(key="k3", value=3),  # 値の同じ書き直しは名乗らない
    ))
    assert moved._base is base._base  # 前提: 基を分け合う道
    assert moved.changed_keys(base) == ("k1", "k2", "new")
    assert base.changed_keys(moved) == ("k1", "k2", "new")
    assert base.changed_keys(base) == ()
    # 等しい表どうしは空(__eq__ と同じ読み)
    assert moved.changed_keys(moved.with_writes((TableWrite(key="k1", value=-1),))) == ()


def test_changed_keys_of_tables_sharing_a_base_reads_only_the_written_rows() -> None:
    base = _counted_table(MIN_DELTA * 20)
    moved = base.with_writes((TableWrite(key="k100", value=_CountedRow(-100)),))
    moved_again = moved.with_writes((TableWrite(key="k200", value=_CountedRow(-200)), TableWrite(key="k300", value=_CountedRow(300))))
    assert moved._base is moved_again._base
    _CountedRow.compared = 0
    # k300 は同じ値の書き直し(別の object)— 値で比べて名乗らない。基の 1,280 行は読まない
    assert moved.changed_keys(moved_again) == ("k200",)
    assert _CountedRow.compared <= 3, "基を分け合う表の比べが書いた鍵の外の行を読んだ"
    # 失敗ケースの照らし: 基を作り直した表との比べは、全部の鍵を読む(同一性の近道が効く行は値を読まない)
    rebuilt = _counted_table(MIN_DELTA * 20)
    _CountedRow.compared = 0
    assert base.changed_keys(rebuilt) == ()
    assert _CountedRow.compared == MIN_DELTA * 20


def test_changed_keys_agrees_with_comparing_the_rows_for_any_writes() -> None:
    # 基を分け合う表(差分と消した鍵だけを読む)・基が作り直された表(全部の鍵を読む)のどちらでも、行の全部の dict の違いと一致する。
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


# ------------------------------------------------------------------ 単価の直し(card E0 — 行数の数え・全行の読み・空の書き・== の断り)


def _model_check(table: Table[int], model: dict[str, int], where: object) -> None:
    assert table.size() == len(model), where
    assert all(table.row(k) == v for k, v in model.items()), where
    assert table.row("absent") is None, where
    keys, rows, items = table.keys(), table.rows(), table.items()
    assert set(keys) == set(model) and len(keys) == len(model), where
    assert dict(zip(keys, rows)) == model, where
    # keys・rows・items は同じ並び。
    assert items == tuple(zip(keys, rows)), where


def test_random_writes_removes_and_compactions_match_a_dict_model() -> None:
    import random

    for seed in range(30):
        rng = random.Random(seed)
        model: dict[str, int] = {f"k{i}": i for i in range(rng.choice((0, 5, 80, 300)))}
        table = table_of(tuple(TableWrite(key=k, value=v) for k, v in model.items()))
        for step in range(40):
            # 少ない書き(2 層のまま)と多い書き(基の作り直し)を混ぜる。
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
    assert big._flat is flat  # 2 度目からは同じ写し
    assert first == big.keys()
    # 基だけの表は基そのもの(写さない)。
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
    # 同じ値でも別の object なら新しい表(同一性で見る)。
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
    # 写像でない物との比べは今まで通り偽。
    assert (table == None) is False  # noqa: E711
    assert (table == 3) is False


def test_copy_and_pickle_keep_the_rows_and_drop_the_flat_copy() -> None:
    import copy
    import pickle

    table = _table(MIN_DELTA * 4).with_writes((TableWrite(key="k3", value=-3), TableWrite(key="k4", value=None)))
    table.keys()  # 写しを作った後でも
    for back in (copy.copy(table), copy.deepcopy(table), pickle.loads(pickle.dumps(table))):
        assert back == table and back.keys() == table.keys() and back.items() == table.items()
        assert back._flat is None or back._flat is not table._flat


# ------------------------------------------------------------------ 系譜(子孫との比べは書きの数に比例する)


def _rows_looked_up(monkeypatch: pytest.MonkeyPatch, compare: Callable[[], object]) -> int:
    """比べ compare が表の行を何回引いたかを数えるため(引いた行の数 = 比べの仕事量)。"""
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
    """1 鍵ずつの書きを writes 回続けて差分の層を育てた表を作るため(本番の拍ごとの畳みの形)。"""
    table = _table(rows)
    for step in range(writes):
        table = table.with_writes((TableWrite(key=f"k{(step * 37) % rows}", value=-step - 1),))
    return table


def test_comparing_a_descendant_reads_only_the_rows_written_between(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = MIN_DELTA * 20
    grown = _grown_chain(rows, rows // COMPACT_RATIO - 2)
    assert len(grown._delta) > 100  # 前提: 差分の層が育っている
    moved = grown.with_writes((TableWrite(key="k5", value=-5000),))
    assert moved._base is grown._base
    # 差分の層の 150 行を読まず、2 つの間に書いた 1 鍵だけを引く(前後の 2 つの表で 1 回ずつ)。
    assert _rows_looked_up(monkeypatch, lambda: moved.changed_keys(grown)) <= 2
    assert _rows_looked_up(monkeypatch, lambda: moved == grown) <= 2
    assert moved.changed_keys(grown) == ("k5",) and grown.changed_keys(moved) == ("k5",)
    assert (moved == grown) is False
    # 祖先との比べも、間に書いた鍵だけ。
    older = _grown_chain(rows, 3)
    assert set(moved.changed_keys(older)) <= {f"k{(step * 37) % rows}" for step in range(rows)} | {"k5"}


def test_the_counterexample_a_sibling_compares_the_whole_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    # 同じ親から作った 2 人目の子は系譜に載らない — 子孫でない表どうしの比べは前の道(差分の層の全部)で、答えは正しい。
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
    assert rebuilt._base is not grown._base  # 前提: この書きで基を作り直した
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
            # 多くは最後の表の子(系譜に載る)・時々は古い表の子(2 人目の子 — 新しい系譜)。
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
