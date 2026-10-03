"""深く凍らせた FrozenMap は、凍らせ直す呼びが中を歩かずにそのまま返す(同じ物)。浅い FrozenMap は今までどおり深く凍らせる。

出自 = 2026-09-28 の実測(agora-controllers の検の速さ): 記録の模擬の一覧の読みと Row の __post_init__ が、既に凍った行の値を
毎回凍らせ直し(282 万回)、自動処理の係の模擬の検の本体の 4 割を占めた。
"""

import copy
import json

import pytest

from doeff_hy.frozen import FrozenMap, freeze_json, freeze_json_text, frozen_json_object, frozen_map_of, thaw_json

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


def test_freeze_json_text_reads_and_freezes_deeply_and_round_trips() -> None:
    # JSON の文字列を読む所で型を決める口(agora-redesign #2628): 読んだ値は freeze-json と同じ深く凍った形で、凍らせ直しは
    # 同じ物を返し、戻すと元の JSON と同じ。
    text = json.dumps(SOURCE)
    frozen = freeze_json_text(text)
    assert isinstance(frozen, FrozenMap)
    assert frozen == freeze_json(SOURCE)
    assert isinstance(frozen["d"], FrozenMap) and frozen["b"] == (1, FrozenMap({"c": "x"}))
    assert freeze_json(frozen) is frozen
    assert thaw_json(frozen) == SOURCE
    assert json.loads(json.dumps(thaw_json(frozen))) == json.loads(text)


def test_freeze_json_text_keeps_leaves_and_arrays_and_refuses_broken_text() -> None:
    assert freeze_json_text('"x"') == "x"
    assert freeze_json_text("[1, [2]]") == (1, (2,))
    with pytest.raises(json.JSONDecodeError):
        freeze_json_text("{broken")


def test_a_deeply_frozen_map_is_shared_by_copy_and_deepcopy() -> None:
    # 深く凍った写像は中まで変えられないので、写しは自分(#2670 — 置き場を丸ごと写す検の土台が変えられない値を作り直さない)。
    frozen = freeze_json(SOURCE)
    assert copy.copy(frozen) is frozen
    assert copy.deepcopy(frozen) is frozen
    held = {"row": frozen}
    assert copy.deepcopy(held)["row"] is frozen


def test_a_map_that_is_not_deeply_frozen_is_still_copied_deeply() -> None:
    # 失敗ケース: 深く凍っていない写像(中に書き換えられる値を持つ)まで共有すると、写しの中の値を書き換えた時に元へ届く。
    inner = [1, 2]
    shallow = FrozenMap({"list": inner})
    copied = copy.deepcopy(shallow)
    assert copied is not shallow
    assert copied == shallow
    assert copied["list"] is not inner
    inner.append(3)
    assert copied["list"] == [1, 2]


# --- items・values は中の dict の眺め(agora-redesign #2670 の根 E (b))---------------------------------------------
# Mapping の既定の ItemsView・ValuesView は鍵ごとに __getitem__ を撃つ。FrozenMap は中の dict の眺めを返す — 中身と順は既定と
# 同じで、凍った値は写さずに同じ物を返し、眺めから中の dict を変えられない。


class _CountingMap(FrozenMap):
    """__getitem__ を呼んだ回数を数える FrozenMap(items・values が鍵ごとの __getitem__ を撃たない事を確かめるため)。"""

    __slots__ = ("_calls",)

    def __init__(self, source: dict[str, object]) -> None:
        super().__init__(source)
        object.__setattr__(self, "_calls", 0)

    def __getitem__(self, key: str) -> object:
        object.__setattr__(self, "_calls", self._calls + 1)
        return super().__getitem__(key)


def test_items_and_values_match_the_mapping_defaults_in_content_and_order() -> None:
    frozen = freeze_json({"z": 1, "a": {"k": [1, 2]}, "m": None})
    from collections.abc import ItemsView, ValuesView

    assert list(frozen.items()) == list(ItemsView(frozen))
    assert list(frozen.values()) == list(ValuesView(frozen))
    assert [k for k, _ in frozen.items()] == ["z", "a", "m"]
    # 凍った値は写さずに同じ物(深く凍った写像の中の写像)。
    inner = frozen["a"]
    assert next(v for k, v in frozen.items() if k == "a") is inner
    assert list(frozen.values())[1] is inner
    assert isinstance(frozen.items(), ItemsView) and isinstance(frozen.values(), ValuesView)


def test_items_and_values_do_not_call_getitem_per_key() -> None:
    counted = _CountingMap({str(i): i for i in range(50)})
    assert sum(v for _, v in counted.items()) == sum(range(50))
    assert sum(counted.values()) == sum(range(50))
    assert counted._calls == 0


def test_the_views_cannot_change_the_frozen_map() -> None:
    frozen = FrozenMap({"a": 1})
    view = frozen.items()
    with pytest.raises(TypeError):
        view.mapping["b"] = 2  # type: ignore[index]  # 眺めの mapping は読むだけ(MappingProxyType)— 書けない事を確かめる
    assert dict(frozen) == {"a": 1}


# --- thaw-json は葉では自分を呼ばない(agora-redesign #2670 の根 E (b) の 3)----------------------------------------
# thaw-json は JSON の節 1 つごとに自分を呼んでいた(葉の文字列・数にも 1 回ずつ)。記録の行を型へ読むたびに全体を戻すので、画面の
# stats の場面で行 1 つあたり約 36 回の呼びになった。葉はその場で返し、自分を呼ぶのは入れ物(写像・列)の時だけにする。

NESTED = {
    "leaf": "s",
    "n": 1,
    "f": 2.5,
    "t": True,
    "none": None,
    "map": {"k": [1, {"x": "y"}, []], "empty": {}},
    "list": [[], {}, "z", [None, False]],
}


def test_thaw_answers_are_unchanged_for_nested_leaves_and_empty_values() -> None:
    thawed = thaw_json(freeze_json(NESTED))
    assert thawed == NESTED
    assert json.dumps(thawed) == json.dumps(NESTED)  # 鍵の順も同じ
    assert type(thawed["map"]) is dict and type(thawed["map"]["empty"]) is dict
    assert type(thawed["map"]["k"]) is list and type(thawed["map"]["k"][2]) is list
    assert type(thawed["list"][0]) is list and type(thawed["list"][1]) is dict
    empty_map = thaw_json(FrozenMap())
    assert empty_map == {} and type(empty_map) is dict
    empty_list = thaw_json(())
    assert empty_list == [] and type(empty_list) is list
    assert thaw_json("x") == "x" and thaw_json(None) is None and thaw_json(2.5) == 2.5
    # 凍っていない値(dict の中の tuple)も同じく戻す。
    assert thaw_json({"a": (1, (2,), {"b": ()})}) == {"a": [1, [2], {"b": []}]}


def test_thaw_answers_keep_leaf_subtypes_as_they_are() -> None:
    import enum

    class Level(enum.IntEnum):
        HIGH = 2

    class Tag(str):
        pass

    tag = Tag("t")
    thawed = thaw_json(FrozenMap({"level": Level.HIGH, "tag": tag, "in": (Level.HIGH, tag)}))
    assert thawed["level"] is Level.HIGH and thawed["tag"] is tag
    assert thawed["in"][0] is Level.HIGH and thawed["in"][1] is tag


def test_thaw_calls_itself_only_for_containers(monkeypatch: pytest.MonkeyPatch) -> None:
    import doeff_hy.frozen as frozen_module

    original = frozen_module.thaw_json
    calls: list[object] = []

    def counted(value: object) -> object:
        calls.append(value)
        return original(value)

    monkeypatch.setattr(frozen_module, "thaw_json", counted)
    value = freeze_json({"a": 1, "b": "x", "c": {"d": True, "e": [1, 2, 3]}, "f": []})
    assert original(value) == {"a": 1, "b": "x", "c": {"d": True, "e": [1, 2, 3]}, "f": []}
    # 一番外は original を直に呼んだ。中で自分を呼ぶのは入れ物の c・e・f の 3 回だけ(葉の 1・"x"・True・1・2・3 では呼ばない)。
    assert len(calls) == 3


# --- 凍らせ直しは深く凍った要素を辿らない(agora-redesign #2670 の根 E の残り)----------------------------------------
# 記録の書きの道の層(行の型の dump → 判定の差分 → 確定する値 → 一覧の行)は、深く凍った値の要素から新しい写像を組み直して凍らせ
# 直す。要素ごとに freeze-json を始めると、行 1 つで同じ値を 5 回辿る(画面の stats の場面で約 110 万回の関数の始まり)。
# 葉と深く凍った写像は freeze-json を呼ばずにその場で返し、凍っていない入れ物は今までどおり全部辿る。


def _counting_freeze(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """module の freeze_json を、受けた値を記録して元へ渡す物に差し替える(中からの呼びの数を数えるため)。"""
    import doeff_hy.frozen as frozen_module

    original = frozen_module.freeze_json
    calls: list[object] = []

    def counted(value: object) -> object:
        calls.append(value)
        return original(value)

    monkeypatch.setattr(frozen_module, "freeze_json", counted)
    return calls


def test_refreezing_a_map_rebuilt_from_frozen_values_does_not_walk_them(monkeypatch: pytest.MonkeyPatch) -> None:
    deep = freeze_json(SOURCE)
    assert isinstance(deep, FrozenMap)
    rebuilt = FrozenMap(dict(deep.items()))  # 判定の差分・確定する値と同じ — 深く凍った値の要素から組み直した、印の無い写像
    calls = _counting_freeze(monkeypatch)
    refrozen = frozen_json_object(rebuilt, "検")
    # 葉の a と深く凍った写像の d では呼ばない。印を持てない列 b だけ 1 回呼び、その中の葉 1 と深く凍った写像 {c} では呼ばない。
    assert calls == [deep["b"]]
    assert refrozen == deep and refrozen is not rebuilt
    assert refrozen["d"] is deep["d"] and refrozen["b"][1] is deep["b"][1]
    assert freeze_json(refrozen) is refrozen


def test_a_value_that_is_not_frozen_is_still_walked_into_every_container(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _counting_freeze(monkeypatch)
    frozen = freeze_json(NESTED)
    # 一番外は直に呼んだ。中で呼ぶのは入れ物の 9 つ(map・map.k・map.k[1]・map.k[2]・map.empty・list・list[0]・list[1]・list[3])。
    assert len(calls) == 9
    assert thaw_json(frozen) == NESTED
    assert isinstance(frozen, FrozenMap) and frozen._deep  # pyright: ignore[reportAttributeAccessIssue] - the deep mark is what this test checks
    inner = frozen["map"]
    assert isinstance(inner, FrozenMap) and inner._deep  # pyright: ignore[reportAttributeAccessIssue] - the deep mark is what this test checks
    assert frozen["list"] == ((), FrozenMap(), "z", (None, False))


def _reference_freeze(value: object) -> object:
    """直す前の freeze-json と同じ答えの、素直な定義(答えが変わらない事を比べるため)。"""
    from collections.abc import Mapping

    if isinstance(value, Mapping):
        return FrozenMap({key: _reference_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_reference_freeze(item) for item in value)
    return value


def _same_shape(left: object, right: object) -> bool:
    """値と型が入れ子の全部で同じか(FrozenMap と tuple と葉の型まで)。"""
    if type(left) is not type(right):
        return False
    if isinstance(left, FrozenMap):
        assert isinstance(right, FrozenMap)
        return list(left) == list(right) and all(_same_shape(left[key], right[key]) for key in left)
    if isinstance(left, tuple):
        assert isinstance(right, tuple)
        return len(left) == len(right) and all(_same_shape(a, b) for a, b in zip(left, right))
    return left == right


def test_the_answers_are_the_same_as_the_plain_definition() -> None:
    import collections
    import enum
    import types

    class Level(enum.IntEnum):
        HIGH = 2

    class Tag(str):
        pass

    deep = freeze_json(SOURCE)
    values: list[object] = [
        SOURCE,
        NESTED,
        deep,
        FrozenMap(dict(deep.items())),
        FrozenMap({"list": [1, {"x": [2]}], "deep": deep}),
        collections.OrderedDict([("b", [1]), ("a", {"c": Level.HIGH})]),
        types.MappingProxyType({"p": (Tag("t"), [None])}),
        [deep, {"q": deep}, (1, [2])],
        (),
        Level.HIGH,
        Tag("t"),
    ]
    for value in values:
        assert _same_shape(freeze_json(value), _reference_freeze(value)), value
    assert _same_shape(frozen_json_object(FrozenMap({"k": [deep]}), "検"), _reference_freeze({"k": [deep]}))
    with pytest.raises(TypeError):
        frozen_json_object([1], "検")
