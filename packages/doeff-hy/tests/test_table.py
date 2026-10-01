"""書き換えない表 Table(doeff_hy.table)の検 — 古い表は書きの後も古い行を読み、基の作り直しの後も変わらない(agora-redesign #2253)。

失敗ケース: 基をその場で書き換える表(下の _InPlaceTable)だと、古い表を持つ読み手が新しい行を読む — 同じ検が赤になる。
"""

from itertools import accumulate

import hy  # noqa: F401  # .hy の module の import hook

from doeff_hy.table import COMPACT_RATIO, MIN_DELTA, Table, TableWrite, table_of


def _rows(n: int) -> tuple[TableWrite[int], ...]:
    """検の材料の行の列を作るため(鍵 k0.. → 値 0..)。"""
    return tuple(TableWrite(key=f"k{i}", value=i) for i in range(n))


def test_an_old_table_keeps_reading_old_rows_after_writes() -> None:
    old = table_of(_rows(10))
    new = old.with_writes((TableWrite(key="k1", value=100), TableWrite(key="k2", value=None), TableWrite(key="x", value=7)))
    assert (old.row("k1"), old.row("k2"), old.row("x"), old.size()) == (1, 2, None, 10)
    assert (new.row("k1"), new.row("k2"), new.row("x"), new.size()) == (100, None, 7, 10)
    assert sorted(new.keys()) == sorted([f"k{i}" for i in range(10) if i != 2] + ["x"])


def test_old_tables_survive_the_rebuild_of_the_base() -> None:
    base_rows = 1000
    old = table_of(_rows(base_rows))
    # 差分の上限(max(MIN_DELTA, 基 / COMPACT_RATIO))を越えるまで 1 行ずつ書き、途中の表を全部持っておく。
    steps = range(max(MIN_DELTA, base_rows // COMPACT_RATIO) + 5)
    kept = tuple(accumulate(steps, lambda table, i: table.with_writes((TableWrite(key=f"k{i}", value=-i),)), initial=old))[1:]
    assert old.row("k0") == 0 and old.row("k3") == 3
    for i, table in zip(steps, kept):
        assert table.row(f"k{i}") == -i
        assert table.row(f"k{i + 1}") == i + 1
    assert kept[-1].size() == base_rows


def test_writing_back_a_removed_key_restores_it() -> None:
    table = table_of(_rows(3)).with_writes((TableWrite(key="k0", value=None),))
    back = table.with_writes((TableWrite(key="k0", value=9),))
    assert (table.row("k0"), table.size()) == (None, 2)
    assert (back.row("k0"), back.size()) == (9, 3)


def test_a_table_cannot_be_changed() -> None:
    table = table_of(_rows(1))
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

    assert not isinstance(table_of(_rows(1)), Mapping)
    assert isinstance(table_of(_rows(1)), Table)
