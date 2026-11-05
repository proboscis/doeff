"""A deeply frozen FrozenMap is returned as is (the same object) by a re-freezing call, without walking its contents. A shallow FrozenMap is still frozen deeply as before.

Origin = measurements on 2026-09-28 (check speed of agora-controllers): the reading of the mock list of records and Row.__post_init__ re-froze the values of already frozen rows
every time (2.82 million times), taking 40% of the body of the automation-role mock check.
"""

import copy
import json

import pytest

from doeff_hy._frozen import FrozenMap, freeze_json, freeze_json_text, frozen_json_object, frozen_map_of, thaw_json

SOURCE = {"a": 1, "b": [1, {"c": "x"}], "d": {"e": None, "f": [True, 2.5]}}


def test_a_deeply_frozen_map_is_returned_as_is() -> None:
    frozen = freeze_json(SOURCE)
    assert freeze_json(frozen) is frozen
    assert frozen_json_object(frozen, "check") is frozen
    assert frozen_map_of(frozen, "check") is frozen
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
    # So that the place that grows a mapping one item at a time (an instrument slice) does not copy everything one Python item at a time with dict(mapping) each time.
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
    # A hook that decides the type where a JSON string is read: the value read is in the same deeply frozen form as freeze-json, re-freezing
    # returns the same object, and converting back gives the original JSON.
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
    # A deeply frozen mapping cannot be changed inside, so a copy is itself (#2670 -- the foundation of the check that copies a whole place does not rebuild unchangeable values).
    frozen = freeze_json(SOURCE)
    assert copy.copy(frozen) is frozen
    assert copy.deepcopy(frozen) is frozen
    held = {"row": frozen}
    assert copy.deepcopy(held)["row"] is frozen


def test_a_map_that_is_not_deeply_frozen_is_still_copied_deeply() -> None:
    # Failure case: if even a mapping that is not deeply frozen (holding a value that can be rewritten) were shared, rewriting a value in the copy would reach the original.
    inner = [1, 2]
    shallow = FrozenMap({"list": inner})
    copied = copy.deepcopy(shallow)
    assert copied is not shallow
    assert copied == shallow
    assert copied["list"] is not inner
    inner.append(3)
    assert copied["list"] == [1, 2]


# --- items and values are views of the inner dict ---------------------------------------------
# The default ItemsView and ValuesView of Mapping call __getitem__ for each key. FrozenMap returns views of the inner dict -- contents and order are the same as the default,
# frozen values are returned as the same object without copying, and the inner dict cannot be changed through the view.


class _CountingMap(FrozenMap):
    """A FrozenMap that counts calls to __getitem__ (to check that items and values do not call __getitem__ per key)."""

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
    # Frozen values are the same object without copying (a mapping inside a deeply frozen mapping).
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
        view.mapping["b"] = 2  # type: ignore[index]  # the view's mapping is read-only (MappingProxyType) -- check that it cannot be written
    assert dict(frozen) == {"a": 1}


# --- thaw-json does not call itself on leaves ----------------------------------------
# thaw-json used to call itself for every JSON node (once each for leaf strings and numbers too). Reading a record row into a type converts the whole thing back, so in the screen's
# stats scenario it became about 36 calls per row. Leaves are returned on the spot, and it calls itself only for containers (mappings, lists).

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
    assert json.dumps(thawed) == json.dumps(NESTED)  # key order is also the same
    assert type(thawed["map"]) is dict and type(thawed["map"]["empty"]) is dict
    assert type(thawed["map"]["k"]) is list and type(thawed["map"]["k"][2]) is list
    assert type(thawed["list"][0]) is list and type(thawed["list"][1]) is dict
    empty_map = thaw_json(FrozenMap())
    assert empty_map == {} and type(empty_map) is dict
    empty_list = thaw_json(())
    assert empty_list == [] and type(empty_list) is list
    assert thaw_json("x") == "x" and thaw_json(None) is None and thaw_json(2.5) == 2.5
    # Values that are not frozen (a tuple inside a dict) are also converted back.
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
    import doeff_hy._frozen as frozen_module

    original = frozen_module.thaw_json
    calls: list[object] = []

    def counted(value: object) -> object:
        calls.append(value)
        return original(value)

    monkeypatch.setattr(frozen_module, "thaw_json", counted)
    value = freeze_json({"a": 1, "b": "x", "c": {"d": True, "e": [1, 2, 3]}, "f": []})
    assert original(value) == {"a": 1, "b": "x", "c": {"d": True, "e": [1, 2, 3]}, "f": []}
    # The outermost called original directly. Inside, it calls itself only for the containers c, e, f -- 3 times (not for the leaves 1, "x", True, 1, 2, 3).
    assert len(calls) == 3


# --- re-freezing does not walk deeply frozen elements ----------------------------------------
# The layers of the record write path (row type dump -> verdict diff -> committed values -> list rows) rebuild a new mapping from the elements of a deeply frozen value and re-freeze
# it. If freeze-json started for every element, one row would walk the same value 5 times (about 1.1 million function entries in the screen stats scenario).
# Leaves and deeply frozen mappings are returned on the spot without calling freeze-json, and containers that are not frozen are walked fully as before.


def _counting_freeze(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Replace the module's freeze_json with one that records the value it receives and passes it on (to count calls from inside)."""
    import doeff_hy._frozen as frozen_module

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
    rebuilt = FrozenMap(dict(deep.items()))  # same as the verdict diff and committed values -- an unmarked mapping rebuilt from the elements of a deeply frozen value
    calls = _counting_freeze(monkeypatch)
    refrozen = frozen_json_object(rebuilt, "check")
    # Not called for the leaf a and the deeply frozen mapping d. Only the list b, which cannot carry a marker, is called once, and not for the leaf 1 or the deeply frozen mapping {c} inside it.
    assert calls == [deep["b"]]
    assert refrozen == deep and refrozen is not rebuilt
    assert refrozen["d"] is deep["d"] and refrozen["b"][1] is deep["b"][1]
    assert freeze_json(refrozen) is refrozen


def test_a_value_that_is_not_frozen_is_still_walked_into_every_container(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _counting_freeze(monkeypatch)
    frozen = freeze_json(NESTED)
    # The outermost was called directly. Inside, it is called for 9 containers (map, map.k, map.k[1], map.k[2], map.empty, list, list[0], list[1], list[3]).
    assert len(calls) == 9
    assert thaw_json(frozen) == NESTED
    assert isinstance(frozen, FrozenMap) and frozen._deep  # pyright: ignore[reportAttributeAccessIssue] - the deep mark is what this test checks
    inner = frozen["map"]
    assert isinstance(inner, FrozenMap) and inner._deep  # pyright: ignore[reportAttributeAccessIssue] - the deep mark is what this test checks
    assert frozen["list"] == ((), FrozenMap(), "z", (None, False))


def _reference_freeze(value: object) -> object:
    """A plain definition giving the same answer as freeze-json before the fix (to compare that answers do not change)."""
    from collections.abc import Mapping

    if isinstance(value, Mapping):
        return FrozenMap({key: _reference_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_reference_freeze(item) for item in value)
    return value


def _same_shape(left: object, right: object) -> bool:
    """Whether values and types are the same throughout the nesting (down to FrozenMap, tuple and leaf types)."""
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
    assert _same_shape(frozen_json_object(FrozenMap({"k": [deep]}), "check"), _reference_freeze({"k": [deep]}))
    with pytest.raises(TypeError):
        frozen_json_object([1], "check")
