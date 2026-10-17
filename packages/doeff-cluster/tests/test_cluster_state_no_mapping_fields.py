"""ClusterState の欄に写像の型(dict・Mapping・FrozenMap)が 0 であることの網(再発の網 — card ki-aa9ec1f5e1d8・#2708 の G)。

12 欄(workers・tasks・placements・surges・drains・board・meta・rollouts・refused・programs・warms・handoffs)は Table[V] へ移した
(#2708 の T1〜T6)。写像の欄を足し直すと、1 行の書きで全行を作り直す費用(設計 4.3)が戻る。欄の注記は dataclass の実体の型なので、
入れ子の型引数(tuple[dict[...], ...] や X | None の中)も歩いて探す。
"""

import collections.abc
import dataclasses
import typing
from dataclasses import dataclass

import hy  # noqa: F401  (Hy の module を読むため)
from doeff_hy.frozen import FrozenMap
from doeff_hy.table import Table

from doeff_cluster.coordinator.intent.cluster_model import ClusterState

MAPPING_ROOTS = (dict, collections.abc.Mapping, FrozenMap)

# 写像の型を持ってよい欄は無い。例外を足す時は理由を書く(足さずに Table にする)。
ALLOWED: dict[str, str] = {}


def _is_mapping_type(tp: object) -> bool:
    return isinstance(tp, type) and issubclass(tp, MAPPING_ROOTS)


def mapping_types_in(tp: object) -> list[object]:
    """型 tp の中(入れ子の型引数も)の写像の型を返す。Table は写像でなく表(Table[V] の V の中は歩く)。"""
    origin = typing.get_origin(tp) or tp
    found: list[object] = []
    if origin is not Table and _is_mapping_type(origin):
        found.append(tp)
    for arg in typing.get_args(tp):
        if arg is Ellipsis:
            continue
        found.extend(mapping_types_in(arg))
    return found


def mapping_fields_of(cls: type) -> dict[str, list[object]]:
    hints = typing.get_type_hints(cls)
    return {
        f.name: found
        for f in dataclasses.fields(cls)
        if f.name not in ALLOWED and (found := mapping_types_in(hints[f.name]))
    }


def test_cluster_state_has_no_mapping_fields() -> None:
    assert mapping_fields_of(ClusterState) == {}


def test_the_twelve_moved_fields_are_tables() -> None:
    moved = (
        "workers tasks placements surges drains board meta rollouts refused programs warms handoffs group_limits"
    ).split()
    hints = typing.get_type_hints(ClusterState)
    assert [name for name in moved if typing.get_origin(hints[name]) is not Table] == []


def test_the_net_catches_a_mapping_field() -> None:
    # 網が効く事の確かめ: 写像の欄を持つ dataclass は引っかかる(Table・tuple・Optional は引っかからない)。
    @dataclass(frozen=True)
    class Bad:
        a: dict[str, int]
        b: collections.abc.Mapping[str, int]
        c: FrozenMap
        d: tuple[dict[str, int], ...]
        e: "dict[str, int] | None"
        ok1: Table[int]
        ok2: tuple[int, ...]
        ok3: int | None

    assert sorted(mapping_fields_of(Bad)) == ["a", "b", "c", "d", "e"]
