"""深く凍らせた FrozenMap は、凍らせ直す呼びが中を歩かずにそのまま返す(同じ物)。浅い FrozenMap は今までどおり深く凍らせる。

出自 = 2026-09-28 の実測(agora-controllers の検の速さ): 記録の模擬の一覧の読みと Row の __post_init__ が、既に凍った行の値を
毎回凍らせ直し(282 万回)、自動処理の係の模擬の検の本体の 4 割を占めた。
"""

from doeff_hy.frozen import FrozenMap, freeze_json, frozen_json_object, frozen_map_of, thaw_json

SOURCE = {"a": 1, "b": [1, {"c": "x"}], "d": {"e": None, "f": [True, 2.5]}}


def test_a_deeply_frozen_map_is_returned_as_is() -> None:
    frozen = freeze_json(SOURCE)
    assert freeze_json(frozen) is frozen
    assert frozen_json_object(frozen, "検") is frozen
    assert frozen_map_of(frozen, "検") is frozen
    assert thaw_json(frozen) == SOURCE


def test_a_shallow_frozen_map_is_still_frozen_deeply() -> None:
    shallow = FrozenMap({"list": [1, 2], "map": {"k": [3]}})
    deep = freeze_json(shallow)
    assert deep is not shallow
    assert deep["list"] == (1, 2)
    assert isinstance(deep["map"], FrozenMap)
    assert deep["map"]["k"] == (3,)
    assert freeze_json(deep) is deep


def test_thaw_returns_fresh_mutable_values() -> None:
    frozen = freeze_json(SOURCE)
    thawed = thaw_json(frozen)
    assert isinstance(thawed["b"], list) and isinstance(thawed["b"][1], dict)
    thawed["b"].append(3)
    assert thaw_json(frozen) == SOURCE


def test_updated_returns_a_new_map_with_the_changes_and_leaves_the_source_alone() -> None:
    # 写像を 1 項ずつ育てる所(計器の断面)が、毎回 dict(写像) で全部を Python の 1 項ずつで写さずに済むため(agora-redesign #2593)。
    source = FrozenMap({"a": 1.0, "b": 2.0})
    grown = source.updated({"b": 3.0, "c": 4.0})
    assert grown == {"a": 1.0, "b": 3.0, "c": 4.0}
    assert isinstance(grown, FrozenMap)
    assert source == {"a": 1.0, "b": 2.0}
    assert source.updated({}) == source


def test_updated_refuses_a_key_that_is_not_a_string() -> None:
    import pytest

    with pytest.raises(TypeError):
        FrozenMap({"a": 1}).updated({1: 2})  # pyright: ignore[reportArgumentType] - the refusal of a non-string key is what this test checks
