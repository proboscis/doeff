"""書き換えない表 Table(doeff_hy.table)の検 — 古い表は書きの後も古い行を読み、基の作り直しの後も変わらない(agora-redesign #2253)。

失敗ケース: 基をその場で書き換える表(下の _InPlaceTable)だと、古い表を持つ読み手が新しい行を読む — 同じ検が赤になる。
"""

from itertools import accumulate

import hy  # noqa: F401  # .hy の module の import hook

from doeff_hy.table import COMPACT_RATIO, MIN_DELTA, Table, TableWrite, table_of


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


# ------------------------------------------------------------------ 下書き TableDraft(agora-redesign #2254)


def test_a_draft_reads_its_own_writes_and_leaves_the_table_alone() -> None:
    from doeff_hy.table import draft_of

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
    from doeff_hy.table import draft_of

    table = _table(3)
    assert draft_of(table).freeze() is table


def test_a_draft_takes_many_writes_in_one_freeze() -> None:
    from doeff_hy.table import draft_of

    # 一覧の拍の形: 空の表へ数万件を書き、書いた行を同じ拍の中で読み直す。
    draft = draft_of(table_of(()))
    for i in range(20000):
        draft.put(f"k{i}", i)
        assert draft.row(f"k{i}") == i
    frozen = draft.freeze()
    assert (frozen.size(), frozen.row("k19999"), frozen.row("k0")) == (20000, 19999, 0)


def test_removing_then_putting_back_in_a_draft_restores_the_row() -> None:
    from doeff_hy.table import draft_of

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


# ------------------------------------------------------------------ 値の列 rows(agora-redesign #2254)


def test_rows_follow_the_keys_after_writes_and_removals() -> None:
    from doeff_hy.table import draft_of

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


# ------------------------------------------------------------------ 値としての扱い(agora-redesign #2254)


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
    # agora-redesign #2412: with-writes と下書きの freeze は書きを 1 度に当てる。基の大きさ・書きの数を変え(作り直しの前と後の両方)、
    # どの表も、列の順に 1 件ずつ当てた dict と同じ行を持つ。
    from doeff_hy.table import draft_of

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


# ------------------------------------------------------------------ 速さの道(agora-redesign #2715 — #2708 の I0a・契約は変えない)


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
